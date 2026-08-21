import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from paperwright.article_model import (
    ARTICLE_MODEL_CONTRACT_VERSION,
    article_model_to_reader,
    render_article_markdown,
    validate_article_model,
)
from paperwright.article_tree import (
    ARTICLE_TREE_CONTRACT_VERSION,
    article_tree_to_article_model,
    validate_final_article_tree,
)
from paperwright.cli import main
from paperwright.exceptions import ContractValidationError
from paperwright.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutPage,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from paperwright.layout_writer import (
    CrossPageParagraphBlock,
    materialize_layout_sources,
    _bind_caption_regions,
    _clean_user_markdown,
    _detect_native_matrix_equations,
    _format_caption_markdown,
    _image_alt_text,
    _merge_cross_page_paragraph_blocks,
    _text_region_non_text_diagnostics,
    _validate_materialized_semantics,
)
from paperwright.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paperwright.layout_risk import LayoutRiskAssessment, PageLayoutRisk
from paperwright.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    sha256_file,
    validate_manifest,
)
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.reader import READER_CONTRACT_VERSION, validate_reader_index
from paperwright.text_reconstruction import ReconstructedText

from pdf_fixture_factory import create_born_digital_fixture


def _write_fixture_reviews(review_root: Path) -> None:
    for page_root in sorted(review_root.glob("page-*")):
        task = LayoutTask.from_dict(
            json.loads(
                (page_root / "layout-task.json").read_text(encoding="utf-8")
            )
        )
        regions = []
        actions = []
        order = 1
        region_id_by_candidate = {
            candidate.candidate_id: f"R{index:03d}"
            for index, candidate in enumerate(task.candidates, start=1)
        }
        caption_parent_by_candidate: dict[str, str] = {}
        hinted_visual_roles: dict[str, str] = {}
        for hint in task.metadata.get("semantic_review_hints", ()):
            visual_ids = hint.get("visual_candidate_ids", ())
            caption_ids = hint.get("caption_candidate_ids", ())
            if len(visual_ids) != 1:
                continue
            visual_id = visual_ids[0]
            parent_region_id = region_id_by_candidate[visual_id]
            hinted_visual_roles[visual_id] = hint["visual_role"]
            for caption_id in caption_ids:
                caption_parent_by_candidate[caption_id] = parent_region_id
        for index, candidate in enumerate(task.candidates, start=1):
            region_id = f"R{index:03d}"
            peripheral = bool(candidate.features.get("peripheral_hint"))
            visual = bool(
                {"image", "vector", "raster"} & set(candidate.element_kinds)
            )
            caption = candidate.features.get(
                "high_confidence_caption_kind"
            ) in {"figure", "table"}
            content_class = (
                "exclude"
                if peripheral
                else "text"
                if caption
                else "visual"
                if visual
                else "text"
            )
            role = (
                "footer"
                if peripheral and candidate.bbox.y > 0.5
                else "header"
                if peripheral
                else "caption"
                if caption
                else hinted_visual_roles.get(candidate.candidate_id, "figure")
                if visual
                else "body"
            )
            regions.append(
                LayoutRegion(
                    region_id=region_id,
                    bbox=candidate.bbox,
                    content_class=content_class,
                    role=role,
                    order=None if peripheral else order,
                    source_candidate_ids=(candidate.candidate_id,),
                    parent_region_id=caption_parent_by_candidate.get(
                        candidate.candidate_id
                    ),
                )
            )
            actions.append(
                LayoutAction(
                    action_id=f"A{index:03d}",
                    action="keep",
                    source_candidate_ids=(candidate.candidate_id,),
                    result_region_ids=(region_id,),
                )
            )
            if candidate.candidate_id in caption_parent_by_candidate:
                actions.append(
                    LayoutAction(
                        action_id=f"AC{index:03d}",
                        action="attach-caption",
                        source_candidate_ids=(candidate.candidate_id,),
                        result_region_ids=(region_id,),
                        target_region_id=caption_parent_by_candidate[
                            candidate.candidate_id
                        ],
                    )
                )
            if not peripheral:
                order += 1
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-layout-reviewer",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=tuple(regions),
            actions=tuple(actions),
        )
        (page_root / "final-layout.json").write_text(
            layout.canonical_json(),
            encoding="utf-8",
        )


def _write_visual_direct_fixture_reviews(review_root: Path) -> None:
    for page_root in sorted(review_root.glob("page-*")):
        task = LayoutTask.from_dict(
            json.loads(
                (page_root / "layout-task.json").read_text(encoding="utf-8")
            )
        )
        bbox = NormalizedBBox.from_dict(
            task.metadata["analysis_roi"]["bbox"]
        )
        region_id = f"page-{task.page.page_index + 1:04d}-content"
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-visual-reviewer",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=(
                LayoutRegion(
                    region_id,
                    bbox,
                    "unknown",
                    "unknown",
                    1,
                ),
            ),
            actions=(
                LayoutAction(
                    f"add-{region_id}",
                    "add",
                    result_region_ids=(region_id,),
                    bbox=bbox,
                ),
            ),
        )
        (page_root / "final-layout.json").write_text(
            layout.canonical_json(),
            encoding="utf-8",
        )


class CrossPageParagraphTests(unittest.TestCase):
    def _block(
        self,
        page_index: int,
        trace_index: int,
        text_index: int,
        text: str,
        *,
        role: str = "body",
        ends_soft: bool = False,
        region_id: str | None = None,
        indent_state: str = "unknown",
        caption_binding_key: tuple[int, str] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> CrossPageParagraphBlock:
        return CrossPageParagraphBlock(
            page_index=page_index,
            region_id=region_id or f"R{page_index + 1}",
            trace_index=trace_index,
            text_index=text_index,
            text=text,
            role=role,
            is_bold=False,
            dominant_font="fixture-roman",
            ends_with_pdf_soft_break=ends_soft,
            element_ids=(f"e{page_index + 1}",),
            first_line_indented=indent_state == "indented",
            first_line_indent_state=indent_state,
            caption_binding_key=caption_binding_key,
            bbox=bbox,
        )

    def test_direct_body_continuation_is_merged_across_page_marker(self):
        lines = [
            "<!-- trace p1 -->",
            "The result continues",
            "",
            "<!-- page: 2 -->",
            "",
            "<!-- trace p2 -->",
            "across the next page.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "The result continues"),
                self._block(1, 5, 6, "across the next page."),
            ),
            {0: 0, 1: 3},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["code"], "joined_cross_page_paragraph")
        self.assertIn("The result continues across the next page.", lines)
        # The page marker is provenance and survives the join.
        self.assertIn("<!-- page: 2 -->", lines)

    def test_pdf_soft_break_joins_without_space_across_page(self):
        lines = ["t1", "inter", "", "p2", "", "t2", "action", ""]
        _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "inter", ends_soft=True),
                self._block(1, 5, 6, "action"),
            ),
            {0: 0, 1: 3},
        )
        self.assertIn("interaction", lines)

    def test_aligned_body_regions_are_joined_on_same_page(self):
        lines = [
            "trace-a",
            "These results are of",
            "",
            "trace-b",
            "high quality.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "These results are of",
                    region_id="body-a",
                    bbox=(0.05, 0.85, 0.45, 0.90),
                ),
                self._block(
                    0,
                    3,
                    4,
                    "high quality.",
                    region_id="body-b",
                    indent_state="aligned",
                    bbox=(0.55, 0.10, 0.95, 0.15),
                ),
            ),
            {0: 0},
        )

        self.assertEqual(
            [item["code"] for item in events],
            ["joined_same_page_body_continuation"],
        )
        self.assertIn("These results are of high quality.", lines)

    def test_trace_adjacent_same_column_fragments_are_joined(self):
        # A reviewer-drawn region boundary within one column, with nothing
        # between the fragments in the trace, is a continuation split.
        lines = [
            "trace-a",
            "Body continues",
            "",
            "trace-b",
            "after the split.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "Body continues",
                    region_id="body-a",
                    bbox=(0.05, 0.40, 0.45, 0.48),
                ),
                self._block(
                    0,
                    3,
                    4,
                    "after the split.",
                    region_id="body-b",
                    indent_state="unknown",
                    bbox=(0.05, 0.60, 0.45, 0.68),
                ),
            ),
            {0: 0},
        )
        self.assertEqual(
            [item["code"] for item in events],
            ["joined_same_page_body_continuation"],
        )
        self.assertIn("Body continues after the split.", lines)

    def test_caption_is_a_hard_barrier_for_body_continuation(self):
        lines = [
            "trace-a",
            "Body continues",
            "",
            "trace-caption",
            "Figure 1. Caption",
            "",
            "trace-b",
            "after the figure.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "Body continues",
                    region_id="body-a",
                    bbox=(0.05, 0.40, 0.45, 0.48),
                ),
                self._block(
                    0,
                    3,
                    4,
                    "Figure 1. Caption",
                    role="caption",
                    region_id="caption",
                ),
                self._block(
                    0,
                    6,
                    7,
                    "after the figure.",
                    region_id="body-b",
                    indent_state="aligned",
                    bbox=(0.05, 0.60, 0.45, 0.68),
                ),
            ),
            {0: 0},
        )

        self.assertEqual(events, [])

    def test_figure_between_columns_does_not_block_body_continuation(self):
        lines = [
            "trace-a",
            "Along with tumor-promoting inflammation",
            "",
            "trace-caption",
            "**Figure 1.** Landscape.",
            "",
            "trace-b",
            "and immune evasion, become appreciated.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "Along with tumor-promoting inflammation",
                    region_id="left",
                    bbox=(0.05, 0.88, 0.45, 0.93),
                ),
                self._block(
                    0,
                    3,
                    4,
                    "**Figure 1.** Landscape.",
                    role="caption",
                    region_id="caption",
                    caption_binding_key=(0, "figure-1"),
                ),
                self._block(
                    0,
                    6,
                    7,
                    "and immune evasion, become appreciated.",
                    region_id="right",
                    indent_state="unknown",
                    bbox=(0.55, 0.05, 0.95, 0.10),
                ),
            ),
            {0: 0},
        )
        self.assertEqual(
            [item["code"] for item in events],
            ["joined_same_page_body_continuation"],
        )
        self.assertIn(
            "Along with tumor-promoting inflammation and immune evasion, "
            "become appreciated.",
            lines,
        )
        # The figure caption between the fragments survives the join.
        self.assertIn("**Figure 1.** Landscape.", lines)

    def test_heading_is_a_barrier_for_body_continuation(self):
        lines = [
            "trace-a",
            "Body continues",
            "",
            "trace-heading",
            "## Methods",
            "",
            "trace-b",
            "using standard protocols.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "Body continues", region_id="body-a"),
                self._block(
                    0, 3, 4, "## Methods", role="heading", region_id="head"
                ),
                self._block(
                    0,
                    6,
                    7,
                    "using standard protocols.",
                    region_id="body-b",
                    indent_state="unknown",
                ),
            ),
            {0: 0},
        )
        self.assertEqual(events, [])

    def test_page_edge_strip_is_transparent_for_continuation(self):
        # A short footer line between a page-end fragment and the next
        # page's first fragment (one-page footer variant) must not block
        # the cross-page join.
        lines = [
            "trace-a",
            "We will also",
            "",
            "trace-footer",
            "Cell, April 13, 2023",
            "",
            "<!-- page: 2 -->",
            "",
            "trace-b",
            "discuss where the field is going.",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "We will also", region_id="body-a"),
                self._block(
                    0,
                    3,
                    4,
                    "Cell, April 13, 2023",
                    region_id="footer",
                    bbox=(0.30, 0.955, 0.70, 0.975),
                ),
                self._block(
                    1,
                    7,
                    8,
                    "discuss where the field is going.",
                    region_id="body-b",
                    indent_state="unknown",
                ),
            ),
            {0: 6},
        )
        self.assertEqual(
            [item["code"] for item in events],
            ["joined_cross_page_paragraph"],
        )
        self.assertIn(
            "We will also discuss where the field is going.", lines
        )
        # The footer strip itself survives in the markdown.
        self.assertIn("Cell, April 13, 2023", lines)

    def test_chain_index_shift_does_not_delete_neighbor_block(self):
        # A cross-page body chain and a caption chain are independent. The
        # caption head (higher trace) is processed first; its head replacement
        # shifts the raw indices the cross-page chain's member still uses.
        # The rebuild must not delete the member's neighbor block.
        lines = [
            "trace-a",
            "aa text",
            "",
            "<!-- page: 2 -->",
            "",
            "trace-cap0",
            "**Figure.** C",
            "",
            "trace-cap1",
            "D panel",
            "",
            "trace-x",
            "xx text",
            "",
        ]
        binding = (1, "figure-1")
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "aa text", region_id="h0"),
                self._block(
                    1,
                    5,
                    6,
                    "**Figure.** C",
                    role="caption",
                    region_id="cap",
                    caption_binding_key=binding,
                ),
                self._block(
                    1,
                    8,
                    9,
                    "D panel",
                    role="caption",
                    region_id="cap",
                    caption_binding_key=binding,
                ),
                self._block(1, 11, 12, "xx text", region_id="x1"),
            ),
            {0: 0},
        )
        codes = [item["code"] for item in events]
        self.assertIn("joined_cross_page_paragraph", codes)
        self.assertIn("joined_caption_fragment", codes)
        joined = "\n".join(lines)
        self.assertIn("aa text xx text", joined)
        self.assertIn("**Figure.** C D panel", joined)
        # xx text appears exactly once (merged into the head, not duplicated
        # nor replaced by the deleted neighbor).
        self.assertEqual(joined.count("xx text"), 1)
        self.assertEqual(joined.count("D panel"), 1)

    def test_fragments_of_same_bound_caption_are_joined(self):
        lines = [
            "trace-caption-1",
            "**Figure 3.** Boundaries are conserved in",
            "",
            "trace-caption-2",
            "evolution. a, Overlap of boundaries.",
            "",
        ]
        binding = (0, "figure-3")
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "**Figure 3.** Boundaries are conserved in",
                    role="caption",
                    region_id="caption-3",
                    caption_binding_key=binding,
                ),
                self._block(
                    0,
                    3,
                    4,
                    "evolution. a, Overlap of boundaries.",
                    role="caption",
                    region_id="caption-3",
                    caption_binding_key=binding,
                ),
            ),
            {0: 0},
        )

        self.assertEqual(
            [item["code"] for item in events],
            ["joined_caption_fragment"],
        )
        self.assertIn(
            "**Figure 3.** Boundaries are conserved in evolution. "
            "a, Overlap of boundaries.",
            lines,
        )

    def test_caption_panel_marker_can_follow_terminal_sentence(self):
        binding = (0, "figure-1")
        lines = ["t1", "**Figure 1.** Overview.", "", "t2", "a, First panel.", ""]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "**Figure 1.** Overview.",
                    role="caption",
                    region_id="caption-1",
                    caption_binding_key=binding,
                ),
                self._block(
                    0,
                    3,
                    4,
                    "a, First panel.",
                    role="caption",
                    region_id="caption-1",
                    caption_binding_key=binding,
                ),
            ),
            {0: 0},
        )

        self.assertEqual(events[0]["code"], "joined_caption_fragment")

    def test_caption_sentence_or_different_binding_is_not_joined(self):
        for second_binding in ((0, "figure-2"), (0, "figure-1")):
            lines = ["t1", "**Figure 1.** Complete.", "", "t2", "Details follow.", ""]
            events = _merge_cross_page_paragraph_blocks(
                lines,
                (
                    self._block(
                        0,
                        0,
                        1,
                        "**Figure 1.** Complete.",
                        role="caption",
                        region_id="caption-1",
                        caption_binding_key=(0, "figure-1"),
                    ),
                    self._block(
                        0,
                        3,
                        4,
                        "Details follow.",
                        role="caption",
                        region_id="caption-1",
                        caption_binding_key=second_binding,
                    ),
                ),
                {0: 0},
            )
            self.assertEqual(events, [])

    def test_indented_body_region_is_not_joined(self):
        lines = [
            "trace-a",
            "Body continues",
            "",
            "trace-b",
            "next text",
            "",
        ]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(
                    0,
                    0,
                    1,
                    "Body continues",
                    region_id="body-a",
                ),
                self._block(
                    0,
                    3,
                    4,
                    "next text",
                    region_id="body-b",
                    indent_state="indented",
                ),
            ),
            {0: 0},
        )
        # An indented first line marks a new paragraph and blocks the join;
        # an unknown indent state no longer blocks trace-adjacent joins.
        self.assertEqual(events, [])

    def test_user_markdown_removes_only_internal_trace_comments(self):
        lines = _clean_user_markdown(
            [
                "# Title",
                "",
                "<!-- page: 1 -->",
                "",
                "<!-- layout-region: R1 -->",
                "Body",
                "",
                "<!-- an author-supplied comment -->",
                "",
            ]
        )
        self.assertEqual(
            lines,
            [
                "# Title",
                "",
                "Body",
                "",
                "<!-- an author-supplied comment -->",
            ],
        )

    def test_caption_and_image_text_are_reader_facing(self):
        caption = (
            "Figure 2 | Topological boundaries demonstrate insulation. "
            "Details."
        )
        self.assertEqual(
            _format_caption_markdown(caption),
            "**Figure 2.** Topological boundaries demonstrate insulation. "
            "Details.",
        )
        self.assertEqual(
            _image_alt_text("figure", 2, caption),
            "Figure 2: Topological boundaries demonstrate insulation.",
        )
        self.assertEqual(
            _image_alt_text(
                "figure",
                3,
                "Fig. 1. Study design and workflow. More details.",
            ),
            "Fig. 1: Study design and workflow.",
        )

    def test_terminal_sentence_is_not_merged_across_page(self):
        lines = ["t1", "Complete.", "", "p2", "", "t2", "next", ""]
        events = _merge_cross_page_paragraph_blocks(
            lines,
            (
                self._block(0, 0, 1, "Complete."),
                self._block(1, 5, 6, "next"),
            ),
            {0: 0, 1: 3},
        )
        self.assertEqual(events, [])
        self.assertIn("p2", lines)


class CaptionBindingTests(unittest.TestCase):
    def _caption_element(self, page_index: int, element_id: str) -> Element:
        return Element(
            element_id,
            "text",
            page_index,
            BBox(10, 70, 80, 10),
            Provenance("fixture", "native", element_id),
            text="Figure 1. Example caption",
            metadata={"line_group": 0, "font_name": "fixture-roman"},
        )

    def test_same_page_caption_binds_by_geometry(self):
        caption_element = self._caption_element(0, "caption")
        page = Page(0, 100, 100, 0, (caption_element,))
        document = PhysicalDocument("a" * 64, "fixture", "1", (page,))
        layout = FinalLayout(
            source_sha256="a" * 64,
            page=LayoutPage.from_page(page),
            regions=(
                LayoutRegion(
                    "figure",
                    NormalizedBBox(0.1, 0.1, 0.8, 0.5),
                    "visual",
                    "figure",
                    1,
                ),
                LayoutRegion(
                    "caption",
                    NormalizedBBox(0.1, 0.62, 0.8, 0.1),
                    "text",
                    "caption",
                    2,
                    source_element_ids=(caption_element.element_id,),
                ),
            ),
        )
        by_caption, _, summary = _bind_caption_regions(document, (layout,))
        self.assertEqual(
            by_caption[(0, "caption")].visual_region_id,
            "figure",
        )
        self.assertEqual(summary["status"], "pass")

    def test_full_page_figure_binds_to_next_page_top_caption(self):
        caption_element = self._caption_element(1, "caption")
        pages = (
            Page(0, 100, 100, 0, ()),
            Page(1, 100, 100, 0, (caption_element,)),
        )
        document = PhysicalDocument("b" * 64, "fixture", "1", pages)
        layout_pages = tuple(LayoutPage.from_page(page) for page in pages)
        layouts = (
            FinalLayout(
                source_sha256="b" * 64,
                page=layout_pages[0],
                regions=(
                    LayoutRegion(
                        "figure",
                        NormalizedBBox(0.05, 0.05, 0.9, 0.9),
                        "visual",
                        "figure",
                        1,
                    ),
                ),
            ),
            FinalLayout(
                source_sha256="b" * 64,
                page=layout_pages[1],
                regions=(
                    LayoutRegion(
                        "caption",
                        NormalizedBBox(0.05, 0.05, 0.9, 0.1),
                        "text",
                        "caption",
                        1,
                        source_element_ids=(caption_element.element_id,),
                    ),
                ),
            ),
        )
        by_caption, _, summary = _bind_caption_regions(document, layouts)
        binding = by_caption[(1, "caption")]
        self.assertEqual(binding.visual_page_index, 0)
        self.assertEqual(binding.method, "next_page_top_caption")
        self.assertEqual(summary["status"], "pass")


class LayoutStageDTests(unittest.TestCase):
    def test_visual_direct_regions_receive_native_elements_by_geometry(self):
        provenance = Provenance("fixture", "native", "fixture")
        elements = (
            Element(
                "journal-header", "text", 0, BBox(10, 1, 70, 3),
                provenance, text="Journal header",
            ),
            Element(
                "body-text", "text", 0, BBox(10, 10, 35, 8),
                provenance, text="Body text",
            ),
            Element(
                "figure-image", "image", 0, BBox(10, 40, 80, 45),
                provenance,
            ),
            Element(
                "figure-label", "text", 0, BBox(15, 45, 10, 6),
                provenance, text="A",
            ),
        )
        page = Page(0, 100, 100, 0, elements)
        task = LayoutTask(
            source_sha256="6" * 64,
            page=LayoutPage.from_page(page),
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=(),
            metadata={
                "review_mode": "visual-direct",
                "analysis_roi": {
                    "bbox": {
                        "x": 0.05,
                        "y": 0.05,
                        "width": 0.90,
                        "height": 0.85,
                    },
                    "source": "confirmed:fixture-ai",
                },
            },
        )
        body_bbox = NormalizedBBox(0.05, 0.05, 0.45, 0.20)
        figure_bbox = NormalizedBBox(0.05, 0.35, 0.90, 0.55)
        header_bbox = NormalizedBBox(0.05, 0.00, 0.90, 0.05)
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="visual-fixture",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=(
                LayoutRegion("body", body_bbox, "text", "body", 1),
                LayoutRegion(
                    "figure", figure_bbox, "visual", "figure", 2
                ),
                LayoutRegion(
                    "header", header_bbox, "exclude", "header", None
                ),
            ),
            actions=(
                LayoutAction(
                    "add-body", "add", result_region_ids=("body",),
                    bbox=body_bbox,
                ),
                LayoutAction(
                    "add-figure", "add", result_region_ids=("figure",),
                    bbox=figure_bbox,
                ),
                LayoutAction(
                    "add-header", "add", result_region_ids=("header",),
                    bbox=header_bbox,
                ),
            ),
        )

        materialized = materialize_layout_sources(layout, task, page)
        by_id = {item.region_id: item for item in materialized.regions}

        self.assertEqual(by_id["body"].source_element_ids, ("body-text",))
        self.assertEqual(
            set(by_id["figure"].source_element_ids),
            {"figure-image", "figure-label"},
        )
        self.assertNotIn(
            "journal-header",
            by_id["body"].source_element_ids,
        )
        self.assertEqual(
            by_id["header"].source_element_ids,
            ("journal-header",),
        )

    def test_materialized_body_cannot_contain_explicit_caption(self):
        element = Element(
            "caption-text",
            "text",
            0,
            BBox(10, 70, 80, 10),
            Provenance("fixture", "native", "caption-text"),
            text="Figure 1 | Explicit caption.",
        )
        page = Page(0, 100, 100, 0, (element,))
        document = PhysicalDocument("7" * 64, "fixture", "1", (page,))
        layout = FinalLayout(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(page),
            regions=(
                LayoutRegion(
                    "R1",
                    NormalizedBBox(0.1, 0.7, 0.8, 0.1),
                    "text",
                    "body",
                    1,
                    source_element_ids=(element.element_id,),
                ),
            ),
        )

        with self.assertRaisesRegex(
            ContractValidationError,
            "explicit Figure/Table caption remains",
        ):
            _validate_materialized_semantics(document, (layout,))

    def test_native_matrix_equation_is_detected_from_frame_geometry(self):
        provenance = Provenance("fixture", "native", "fixture")
        elements = []
        rows = (("⎡", "⎤"), ("⎢", "⎥"), ("⎣", "⎦"))
        for row_index, (left, right) in enumerate(rows):
            y = 20.0 + row_index * 8.0
            for matrix_index, x in enumerate((10.0, 35.0, 60.0)):
                elements.extend(
                    (
                        Element(
                            f"l-{row_index}-{matrix_index}",
                            "text",
                            0,
                            BBox(x, y, 2, 7),
                            provenance,
                            text=left,
                        ),
                        Element(
                            f"v-{row_index}-{matrix_index}",
                            "text",
                            0,
                            BBox(x + 5, y + 1, 4, 5),
                            provenance,
                            text=str(row_index + matrix_index),
                        ),
                        Element(
                            f"r-{row_index}-{matrix_index}",
                            "text",
                            0,
                            BBox(x + 12, y, 2, 7),
                            provenance,
                            text=right,
                        ),
                    )
                )
        paragraphs = tuple(
            ReconstructedText(
                text=f"row {row_index}",
                element_ids=tuple(
                    item.element_id
                    for item in elements
                    if item.element_id.split("-")[1] == str(row_index)
                ),
            )
            for row_index in range(3)
        )

        equations = _detect_native_matrix_equations(
            Page(0, 100, 100, 0, tuple(elements)),
            tuple(elements),
            paragraphs,
        )

        self.assertEqual(len(equations), 1)
        self.assertEqual(equations[0].paragraph_indexes, (0, 1, 2))
        self.assertEqual(len(equations[0].element_ids), len(elements))
        self.assertLess(equations[0].bbox.x, 10.0)
        self.assertGreater(equations[0].bbox.right, 74.0)

    def test_text_regions_ignore_rules_and_heading_backgrounds_only(self):
        provenance = Provenance("fixture", "native", "fixture")
        elements = (
            Element("text", "text", 0, BBox(10, 10, 40, 8), provenance),
            Element("rule", "vector", 0, BBox(10, 20, 50, 0.2), provenance),
            Element("background", "vector", 0, BBox(10, 10, 50, 10), provenance),
            Element("image", "image", 0, BBox(60, 10, 20, 20), provenance),
        )
        page = Page(0, 100, 100, 0, elements)
        region = LayoutRegion(
            region_id="R001",
            bbox=NormalizedBBox(0.1, 0.1, 0.5, 0.1),
            content_class="text",
            role="heading",
            order=1,
            source_element_ids=("text", "rule", "background", "image"),
        )

        result = _text_region_non_text_diagnostics(page, region)

        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["ignored_decorative_count"], 2)
        self.assertEqual(result["risk_count"], 1)
        self.assertEqual(result["by_class"]["decorative_rule"], 1)
        self.assertEqual(result["by_class"]["heading_background"], 1)
        self.assertEqual(result["by_class"]["semantic_non_text"], 1)

    def _prepare(
        self,
        root: Path,
        *,
        include_references: bool = False,
        extraction_profile: str = "forensic",
        review_mode: str = "candidate-assisted",
    ) -> tuple[Path, Path]:
        source = root / "fixture.pdf"
        proposal = root / "roi-proposal"
        review = root / "review"
        create_born_digital_fixture(
            source,
            include_references=include_references,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "layout-prepare",
                    str(source),
                    str(proposal),
                    "--workspace-root",
                    str(root),
                    "--extraction-profile",
                    extraction_profile,
                    "--review-mode",
                    review_mode,
                ]
            )
        self.assertEqual(code, 0)
        roi_path = proposal / "content-roi.json"
        roi = json.loads(roi_path.read_text(encoding="utf-8"))
        roi["review_status"] = "confirmed"
        roi["reviewer"] = "fixture-roi-reviewer"
        roi_path.write_text(
            json.dumps(
                roi,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "layout-prepare",
                    str(source),
                    str(review),
                    "--content-roi-json",
                    str(roi_path),
                    "--workspace-root",
                    str(root),
                    "--extraction-profile",
                    extraction_profile,
                    "--review-mode",
                    review_mode,
                ]
            )
        self.assertEqual(code, 0)
        if review_mode == "visual-direct":
            _write_visual_direct_fixture_reviews(review)
        else:
            _write_fixture_reviews(review)
        return source, review

    def test_visual_direct_apply_replays_confirmed_roi_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(
                root,
                extraction_profile="fast",
                review_mode="visual-direct",
            )
            task = json.loads(
                (review / "page-0001" / "layout-task.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(task["candidates"], [])
            self.assertTrue(
                task["metadata"]["analysis_roi"]["source"].startswith(
                    "confirmed:"
                )
            )
            output = root / "visual-direct-output"

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                        "--evidence",
                        "minimal",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue((output / "article.md").is_file())
            self.assertTrue(any((output / "images").glob("*.png")))

    def test_fast_layout_prepare_and_apply_use_recorded_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(
                root,
                extraction_profile="fast",
            )
            output = root / "fast-output"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                        "--evidence",
                        "minimal",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue((output / "article.md").is_file())
            self.assertEqual(
                json.loads(
                    (review / "review-index.json").read_text(encoding="utf-8")
                )["extraction_profile"],
                "fast",
            )

    def test_standard_profile_selectively_escalates_and_replays_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            forced_risk = LayoutRiskAssessment(
                (
                    PageLayoutRisk(
                        page_index=0,
                        reasons=("raster_region_ambiguity_high",),
                        candidate_count=12,
                        raster_candidate_count=9,
                        separator_count=20,
                        native_text_element_count=4,
                    ),
                    PageLayoutRisk(
                        page_index=1,
                        reasons=(),
                        candidate_count=3,
                        raster_candidate_count=1,
                        separator_count=2,
                        native_text_element_count=3,
                    ),
                )
            )
            with mock.patch(
                "paperwright.api.assess_layout_risk",
                return_value=forced_risk,
            ):
                source, review = self._prepare(
                    root,
                    extraction_profile="standard",
                )
            index = json.loads(
                (review / "review-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                index["effective_extraction_profile"],
                "hybrid-standard",
            )
            self.assertEqual(
                index["layout_risk_assessment"]["escalation_page_indices"],
                [0],
            )
            self.assertEqual(
                index["layout_task_versions"],
                [
                    "paperwright-layout-task-v0.1",
                    "paperwright-layout-task-v0.2",
                ],
            )

            output = root / "standard-output"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                        "--evidence",
                        "minimal",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue((output / "article.md").is_file())

    def test_standard_profile_keeps_inventory_when_no_page_escalates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            no_risk = LayoutRiskAssessment(
                tuple(
                    PageLayoutRisk(
                        page_index=index,
                        reasons=(),
                        candidate_count=3,
                        raster_candidate_count=1,
                        separator_count=2,
                        native_text_element_count=3,
                    )
                    for index in range(2)
                )
            )
            with mock.patch(
                "paperwright.api.assess_layout_risk",
                return_value=no_risk,
            ):
                _, review = self._prepare(
                    root,
                    extraction_profile="standard",
                )

            index = json.loads(
                (review / "review-index.json").read_text(encoding="utf-8")
            )
            cached = PhysicalDocument.from_dict(
                json.loads(
                    (
                        review
                        / "extraction-cache"
                        / "physical-document.json"
                    ).read_text(encoding="utf-8")
                )
            )
            self.assertEqual(
                index["effective_extraction_profile"],
                "inventory-standard",
            )
            self.assertEqual(
                index["physical_extraction_profile"],
                "inventory-standard",
            )
            self.assertEqual(
                index["source_evidence"]["contract_version"],
                "paperwright-source-evidence-v0.2",
            )
            self.assertEqual(index["source_evidence"]["provider_count"], 4)
            self.assertTrue((review / "source-evidence" / "index.json").is_file())
            images = [
                element
                for page in cached.pages
                for element in page.elements
                if element.kind == "image"
            ]
            self.assertTrue(images)
            self.assertTrue(
                all(
                    item.metadata.get("asset_materialization") == "deferred"
                    for item in images
                )
            )

            output = root / "inventory-standard-output"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(root / "fixture.pdf"),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                        "--evidence",
                        "minimal",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue((output / "article.md").is_file())

    def test_layout_apply_rejects_tampered_extraction_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(
                root,
                extraction_profile="fast",
            )
            cached_document = (
                review / "extraction-cache" / "physical-document.json"
            )
            cached_document.write_text(
                cached_document.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            output = root / "tampered-cache-output"

            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                        "--evidence",
                        "minimal",
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())

    def test_layout_apply_rejects_unconfirmed_content_roi(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            review = root / "review"
            output = root / "output"
            create_born_digital_fixture(source)
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-prepare",
                        str(source),
                        str(review),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            _write_fixture_reviews(review)
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())

    def test_reference_section_can_be_omitted_or_written_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(
                root,
                include_references=True,
            )
            omitted = root / "omitted"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(omitted),
                        "--references",
                        "omit",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            omitted_article = (omitted / "article.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Smith AB", omitted_article)
            self.assertNotIn("Acknowledgments", omitted_article)
            self.assertNotIn("fixture reviewers", omitted_article)
            self.assertNotIn("Author Contributions", omitted_article)
            self.assertIn("Supplementary Information", omitted_article)
            self.assertIn("Supplementary Figure S1", omitted_article)
            self.assertFalse((omitted / "references.md").exists())

            separated = root / "separated"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(separated),
                        "--references",
                        "separate",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            separated_article = (separated / "article.md").read_text(
                encoding="utf-8"
            )
            references = (separated / "references.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Smith AB", separated_article)
            self.assertNotIn("Acknowledgments", separated_article)
            self.assertNotIn("fixture reviewers", separated_article)
            self.assertNotIn("Author Contributions", separated_article)
            self.assertIn("Supplementary Information", separated_article)
            self.assertIn("Supplementary Figure S1", separated_article)
            self.assertIn("# References", references)
            self.assertIn("Smith AB", references)
            self.assertNotIn("Acknowledgments", references)
            provenance = json.loads(
                (
                    separated
                    / "_paperwright"
                    / "04-provenance"
                    / "layout-provenance.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["references"]["status"], "detected")
            self.assertEqual(
                provenance["references"]["output_path"],
                "references.md",
            )
            self.assertEqual(
                provenance["references"]["detection_method"],
                "heading_and_entries",
            )
            self.assertIsNotNone(
                provenance["references"]["end_page_index"]
            )
            self.assertGreater(
                provenance["references"][
                    "omitted_back_matter_paragraphs"
                ],
                0,
            )

    def test_layout_apply_writes_standard_self_contained_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            output = root / "output"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["manifest_version"],
                HYBRID_LAYOUT_MANIFEST_VERSION,
            )
            self.assertTrue((output / "article.md").is_file())
            self.assertTrue(any((output / "images").glob("*.png")))
            self.assertTrue((output / "images" / "figure-0001.png").is_file())
            manifest = json.loads(
                (
                    output / "_paperwright" / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            self.assertFalse(manifest["layout_review"]["ocr_used"])
            self.assertEqual(
                manifest["layout_review"]["evidence_level"],
                "standard",
            )
            provenance = output / manifest["layout_review"]["provenance_path"]
            self.assertEqual(
                sha256_file(provenance),
                manifest["layout_review"]["provenance_sha256"],
            )
            article = (output / "article.md").read_text(encoding="utf-8")
            self.assertIn("<!-- pwwd:block", article)
            self.assertIn("<!-- pwwd:slot", article)
            self.assertNotIn("<!-- page:", article)
            self.assertNotIn("<!-- layout-region:", article)
            self.assertNotIn("<!-- caption-for:", article)
            self.assertNotIn("<!-- cross-page-continuation:", article)
            self.assertNotIn("; elements: ", article)
            provenance_value = json.loads(provenance.read_text(encoding="utf-8"))
            self.assertEqual(
                provenance_value["contract_version"],
                "paperwright-layout-provenance-v0.5",
            )
            self.assertIn("body_continuation_repairs", provenance_value)
            self.assertIn("caption_continuation_repairs", provenance_value)
            self.assertIn("cross_page_repairs", provenance_value)
            self.assertTrue(
                any(
                    region["source_element_ids"]
                    for page in provenance_value["pages"]
                    for region in page["regions"]
                )
            )
            self.assertTrue(
                any(
                    paragraph.get("article_block_id")
                    for page in provenance_value["pages"]
                    for region in page["regions"]
                    for paragraph in region.get("paragraphs", ())
                )
            )
            reader_path = output / manifest["reader"]["path"]
            reader = json.loads(reader_path.read_text(encoding="utf-8"))
            validate_reader_index(reader, article_text=article, root=output)
            self.assertEqual(
                reader["contract_version"], READER_CONTRACT_VERSION
            )
            self.assertEqual(
                sha256_file(reader_path), manifest["reader"]["sha256"]
            )
            self.assertEqual(
                reader["article"]["sha256"],
                manifest["reader"]["article_sha256"],
            )
            article_model_path = output / manifest["article_model"]["path"]
            article_model = json.loads(
                article_model_path.read_text(encoding="utf-8")
            )
            validate_article_model(article_model, root=output)
            self.assertEqual(
                article_model["contract_version"],
                ARTICLE_MODEL_CONTRACT_VERSION,
            )
            self.assertEqual(render_article_markdown(article_model), article)
            self.assertEqual(article_model_to_reader(article_model), reader)
            self.assertEqual(
                sha256_file(article_model_path),
                manifest["article_model"]["sha256"],
            )
            article_tree_path = output / "_paperwright" / "article-tree.json"
            article_tree = json.loads(
                article_tree_path.read_text(encoding="utf-8")
            )
            validate_final_article_tree(article_tree, root=output)
            self.assertEqual(
                article_tree["contract_version"],
                ARTICLE_TREE_CONTRACT_VERSION,
            )
            self.assertEqual(
                article_tree_to_article_model(article_tree),
                article_model,
            )
            self.assertFalse(
                (
                    output
                    / "_paperwright"
                    / "01-physical"
                    / "physical-document.json"
                ).exists()
            )
            self.assertTrue(
                (output / "_paperwright" / "02-roi" / "content-roi.json").is_file()
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright"
                    / "02-structure"
                    / "paper-recipe.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright"
                    / "02-structure"
                    / "source-element-tree.json"
                ).is_file()
            )
            self.assertIn(
                "paper_recipe",
                {item["role"] for item in manifest["outputs"]},
            )
            self.assertIn(
                "article_tree",
                {item["role"] for item in manifest["outputs"]},
            )
            self.assertIn(
                "source_element_tree",
                {item["role"] for item in manifest["outputs"]},
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright"
                    / "03-layout"
                    / "page-0001-overlay.png"
                ).is_file()
            )
            validation = json.loads(
                (
                    output
                    / "_paperwright"
                    / "05-validation"
                    / "validation-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(validation["checks"]["ocr_not_used"])
            self.assertEqual(
                set(validation["quality_checks"]),
                {
                    "markdown_text",
                    "word_spacing",
                    "caption_binding",
                    "figure_label_leakage",
                    "semantic_layout",
                    "title_integrity",
                    "image_links",
                    "layout_element_coverage",
                    "layout_element_uniqueness",
                    "markdown_exclusions",
                    "manifest_inventory",
                    "native_object_diagnostics",
                    "page_completeness",
                    "text_reconstruction",
                    "reader_index",
                    "article_tree",
                    "article_model",
                },
            )
            self.assertEqual(
                validation["quality_checks"]["image_links"]["status"],
                "pass",
            )
            self.assertEqual(
                validation["quality_checks"]["text_reconstruction"]["status"],
                "pass",
            )
            self.assertIn("warning_summary", validation)
            self.assertIn(
                "actionable_findings",
                validation["warning_summary"],
            )
            self.assertTrue(
                validation["checks"]["manifest_inventory_complete"]
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright"
                    / "03-layout"
                    / "page-0001-final-layout.json"
                ).is_file()
            )
            self.assertFalse(
                any(
                    (output / "_paperwright" / "03-layout").glob(
                        "*-layout-task.json"
                    )
                )
            )
            self.assertTrue((output / "_paperwright" / "run.json").is_file())
            self.assertTrue((output / "_paperwright" / "source.json").is_file())
            self.assertTrue(
                (
                    output
                    / "_paperwright"
                    / "05-validation"
                    / "validation-report.md"
                ).is_file()
            )

    def test_layout_apply_supports_minimal_and_full_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            minimal = root / "minimal"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(minimal),
                        "--evidence",
                        "minimal",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                {item.name for item in minimal.iterdir()},
                {"article.md", "images", "_paperwright"},
            )
            self.assertEqual(
                {
                    item.relative_to(minimal).as_posix()
                    for item in (minimal / "_paperwright").rglob("*")
                    if item.is_file()
                },
                {
                    "_paperwright/article-tree.json",
                    "_paperwright/article-model.json",
                    "_paperwright/completeness-report.json",
                    "_paperwright/manifest.json",
                    "_paperwright/reader.json",
                },
            )
            minimal_manifest = json.loads(
                (minimal / "_paperwright" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                minimal_manifest["layout_review"]["evidence_level"],
                "minimal",
            )
            self.assertIsNone(
                minimal_manifest["layout_review"]["provenance_path"]
            )
            self.assertTrue((minimal / "_paperwright" / "reader.json").is_file())
            self.assertEqual(
                minimal_manifest["reader"]["contract_version"],
                READER_CONTRACT_VERSION,
            )

            full = root / "full"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(full),
                        "--evidence",
                        "full",
                        "--include-source-pdf",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            evidence = full / "_paperwright"
            self.assertTrue(
                (evidence / "01-physical" / "physical-document.json").is_file()
            )
            self.assertTrue(
                (evidence / "02-roi" / "page-0001-content-roi.png").is_file()
            )
            self.assertTrue(
                (evidence / "03-layout" / "page-0001-layout-task.json").is_file()
            )
            self.assertTrue(
                (evidence / "03-layout" / "page-0001-page.png").is_file()
            )
            self.assertEqual(
                (evidence / "source.pdf").read_bytes(), source.read_bytes()
            )

    def test_layout_apply_core_artifacts_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            outputs = []
            for name in ("output-a", "output-b"):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            "layout-apply",
                            str(source),
                            str(review),
                            str(root / name),
                            "--workspace-root",
                            str(root),
                        ]
                    )
                self.assertEqual(code, 0)
                outputs.append(root / name)

            def content(output_root: Path):
                return {
                    path.relative_to(output_root).as_posix(): path.read_bytes()
                    for path in output_root.rglob("*")
                    if path.is_file()
                    and path.name not in {"run.json", "manifest.json"}
                }

            self.assertEqual(content(outputs[0]), content(outputs[1]))
            for output in outputs:
                run = json.loads(
                    (output / "_paperwright" / "run.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(run["completed_at_utc"].endswith("+00:00"))

    def test_layout_apply_rejects_stale_task_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            task_path = review / "page-0001" / "layout-task.json"
            value = json.loads(task_path.read_text(encoding="utf-8"))
            value["candidate_generator_version"] = "stale-generator"
            task_path.write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
            output = root / "output"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
