import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from paper2md.cli import main
from paper2md.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutPage,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from paper2md.layout_writer import (
    CrossPageParagraphBlock,
    _bind_caption_regions,
    _clean_user_markdown,
    _detect_native_matrix_equations,
    _format_caption_markdown,
    _image_alt_text,
    _merge_cross_page_paragraph_blocks,
    _text_region_non_text_diagnostics,
)
from paper2md.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paper2md.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    sha256_file,
    validate_manifest,
)
from paper2md.models import BBox, Element, Page, PhysicalDocument, Provenance
from paper2md.text_reconstruction import ReconstructedText

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
        for index, candidate in enumerate(task.candidates, start=1):
            region_id = f"R{index:03d}"
            peripheral = bool(candidate.features.get("peripheral_hint"))
            visual = bool(
                {"image", "vector"} & set(candidate.element_kinds)
            )
            content_class = (
                "exclude" if peripheral else "visual" if visual else "text"
            )
            role = (
                "footer"
                if peripheral and candidate.bbox.y > 0.5
                else "header"
                if peripheral
                else "figure"
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
        self.assertNotIn("<!-- page: 2 -->", lines)

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
                ),
                self._block(
                    0,
                    3,
                    4,
                    "high quality.",
                    region_id="body-b",
                    indent_state="aligned",
                ),
            ),
            {0: 0},
        )

        self.assertEqual(
            [item["code"] for item in events],
            ["joined_same_page_body_continuation"],
        )
        self.assertIn("These results are of high quality.", lines)

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
                self._block(0, 0, 1, "Body continues", region_id="body-a"),
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
                ),
            ),
            {0: 0},
        )

        self.assertEqual(events, [])

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

    def test_indented_or_unknown_body_region_is_not_joined(self):
        for indent_state in ("indented", "unknown"):
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
                        indent_state=indent_state,
                    ),
                ),
                {0: 0},
            )
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
                ]
            )
        self.assertEqual(code, 0)
        _write_fixture_reviews(review)
        return source, review

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
                    / "_paper2md"
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
                    output / "_paper2md" / "manifest.json"
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
            self.assertNotIn("<!-- page:", article)
            self.assertNotIn("<!-- layout-region:", article)
            self.assertNotIn("<!-- caption-for:", article)
            self.assertNotIn("<!-- cross-page-continuation:", article)
            self.assertNotIn("; elements: ", article)
            provenance_value = json.loads(provenance.read_text(encoding="utf-8"))
            self.assertEqual(
                provenance_value["contract_version"],
                "paper2md-layout-provenance-v0.4",
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
            self.assertFalse(
                (
                    output
                    / "_paper2md"
                    / "01-physical"
                    / "physical-document.json"
                ).exists()
            )
            self.assertTrue(
                (output / "_paper2md" / "02-roi" / "content-roi.json").is_file()
            )
            self.assertTrue(
                (
                    output
                    / "_paper2md"
                    / "03-layout"
                    / "page-0001-overlay.png"
                ).is_file()
            )
            validation = json.loads(
                (
                    output
                    / "_paper2md"
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
                    "title_integrity",
                    "image_links",
                    "layout_element_coverage",
                    "layout_element_uniqueness",
                    "markdown_exclusions",
                    "manifest_inventory",
                    "native_object_diagnostics",
                    "text_reconstruction",
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
                    / "_paper2md"
                    / "03-layout"
                    / "page-0001-final-layout.json"
                ).is_file()
            )
            self.assertFalse(
                any(
                    (output / "_paper2md" / "03-layout").glob(
                        "*-layout-task.json"
                    )
                )
            )
            self.assertTrue((output / "_paper2md" / "run.json").is_file())
            self.assertTrue((output / "_paper2md" / "source.json").is_file())
            self.assertTrue(
                (
                    output
                    / "_paper2md"
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
                {"article.md", "images", "_paper2md"},
            )
            self.assertEqual(
                {
                    item.relative_to(minimal).as_posix()
                    for item in (minimal / "_paper2md").rglob("*")
                    if item.is_file()
                },
                {"_paper2md/manifest.json"},
            )
            minimal_manifest = json.loads(
                (minimal / "_paper2md" / "manifest.json").read_text(
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
            evidence = full / "_paper2md"
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
                    (output / "_paper2md" / "run.json").read_text(
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
