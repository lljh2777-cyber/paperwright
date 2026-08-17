from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from paperwright.cross_page_caption import (
    CROSS_PAGE_CAPTION_PROMPT_VERSION,
    CROSS_PAGE_CAPTION_REVIEW_VERSION,
    build_cross_page_caption_task,
    canonical_cross_page_caption_review_json,
    canonical_cross_page_caption_task_json,
    compile_cross_page_caption_review,
    cross_page_caption_task_sha256,
    validate_cross_page_caption_review,
)
from paperwright.exceptions import ContractValidationError
from paperwright.issue_routing import (
    ISSUE_CAPTION_VISUAL_BINDING,
    ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING,
    plan_issue_routing,
)
from paperwright.layout_caption import bind_caption_regions
from paperwright.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutCandidate,
    LayoutPage,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from paperwright.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paperwright.layout_writer import (
    materialize_layout_sources,
    write_layout_outputs,
)
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.region_render import RegionRenderResult


SHA = "c" * 64


def _document() -> PhysicalDocument:
    provenance = Provenance("fixture", "native", "fixture")
    caption = Element(
        "caption",
        "text",
        1,
        BBox(10, 5, 80, 10),
        provenance,
        text="Figure 7. Cross-page result",
    )
    pages = (
        Page(0, 100, 100, 0, ()),
        Page(1, 100, 100, 0, (caption,)),
    )
    return PhysicalDocument(SHA, "fixture", "1", pages)


def _layouts(document: PhysicalDocument) -> tuple[FinalLayout, ...]:
    return (
        FinalLayout(
            source_sha256=SHA,
            page=LayoutPage.from_page(document.pages[0]),
            regions=(
                LayoutRegion(
                    "figure",
                    NormalizedBBox(0.05, 0.12, 0.9, 0.82),
                    "visual",
                    "figure",
                    1,
                ),
            ),
        ),
        FinalLayout(
            source_sha256=SHA,
            page=LayoutPage.from_page(document.pages[1]),
            regions=(
                LayoutRegion(
                    "caption",
                    NormalizedBBox(0.05, 0.05, 0.9, 0.1),
                    "text",
                    "caption",
                    1,
                    source_element_ids=("caption",),
                ),
            ),
        ),
    )


def _text(_page: Page, region: LayoutRegion) -> str:
    return "Figure 7. Cross-page result" if region.region_id == "caption" else ""


class CrossPageCaptionTests(unittest.TestCase):
    def setUp(self):
        self.document = _document()
        self.layouts = _layouts(self.document)
        self.task = build_cross_page_caption_task(
            self.document,
            self.layouts,
            caption_text=_text,
        )

    def _review(self, *, reject: bool = False):
        pair = self.task["pairs"][0]
        return {
            "contract_version": CROSS_PAGE_CAPTION_REVIEW_VERSION,
            "source_sha256": SHA,
            "task_sha256": cross_page_caption_task_sha256(self.task),
            "reviewer": "fixture-reviewer",
            "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
            "bindings": [] if reject else [
                {
                    "caption_ref": pair["caption"]["caption_ref"],
                    "visual_ref": pair["visual_candidates"][0]["visual_ref"],
                    "confidence": 0.95,
                }
            ],
            "rejected_caption_refs": (
                [pair["caption"]["caption_ref"]] if reject else []
            ),
            "warnings": [],
        }

    def test_builds_adjacent_page_relation_candidate(self):
        self.assertEqual(len(self.task["pairs"]), 1)
        pair = self.task["pairs"][0]
        self.assertEqual(pair["caption"]["caption_ref"], "p0002:caption")
        self.assertEqual(
            pair["visual_candidates"][0]["visual_ref"],
            "p0001:figure",
        )

    def test_builds_bottom_caption_when_previous_visual_is_large(self):
        bottom_caption = LayoutRegion(
            "caption",
            NormalizedBBox(0.05, 0.80, 0.9, 0.1),
            "text",
            "caption",
            1,
            source_element_ids=("caption",),
        )
        layouts = (self.layouts[0], FinalLayout(
            source_sha256=SHA,
            page=self.layouts[1].page,
            regions=(bottom_caption,),
        ))
        task = build_cross_page_caption_task(
            self.document,
            layouts,
            caption_text=_text,
        )
        self.assertEqual(len(task["pairs"]), 1)

    def test_does_not_cross_bind_when_caption_page_has_local_visual(self):
        local_figure = LayoutRegion(
            "local-figure",
            NormalizedBBox(0.05, 0.01, 0.9, 0.3),
            "visual",
            "figure",
            1,
        )
        local_caption = LayoutRegion(
            "caption",
            self.layouts[1].regions[0].bbox,
            "text",
            "caption",
            2,
            source_element_ids=("caption",),
        )
        layouts = (self.layouts[0], FinalLayout(
            source_sha256=SHA,
            page=self.layouts[1].page,
            regions=(local_figure, local_caption),
        ))
        task = build_cross_page_caption_task(
            self.document,
            layouts,
            caption_text=_text,
        )
        self.assertEqual(task["pairs"], [])

    def test_panel_continuity_overrides_page_local_visual_suppression(self):
        local_figure = LayoutRegion(
            "local-figure",
            NormalizedBBox(0.05, 0.05, 0.9, 0.2),
            "visual",
            "figure",
            1,
        )
        local_caption = LayoutRegion(
            "caption",
            NormalizedBBox(0.05, 0.27, 0.9, 0.1),
            "text",
            "caption",
            2,
            source_element_ids=("caption",),
        )
        layouts = (
            self.layouts[0],
            FinalLayout(
                source_sha256=SHA,
                page=self.layouts[1].page,
                regions=(local_figure, local_caption),
            ),
        )
        task = build_cross_page_caption_task(
            self.document,
            layouts,
            caption_text=_text,
        )
        self.assertEqual(len(task["pairs"]), 1)
        self.assertIn(
            "cross_page_panel_continuity",
            task["pairs"][0]["signals"],
        )

    def test_previous_local_caption_terminates_panel_continuity(self):
        previous_caption = LayoutRegion(
            "previous-caption",
            NormalizedBBox(0.05, 0.82, 0.9, 0.08),
            "text",
            "caption",
            2,
        )
        current_figure = LayoutRegion(
            "local-figure",
            NormalizedBBox(0.05, 0.05, 0.9, 0.2),
            "visual",
            "figure",
            1,
        )
        current_caption = LayoutRegion(
            "caption",
            NormalizedBBox(0.05, 0.27, 0.9, 0.1),
            "text",
            "caption",
            2,
            source_element_ids=("caption",),
        )
        layouts = (
            FinalLayout(
                source_sha256=SHA,
                page=self.layouts[0].page,
                regions=(self.layouts[0].regions[0], previous_caption),
            ),
            FinalLayout(
                source_sha256=SHA,
                page=self.layouts[1].page,
                regions=(current_figure, current_caption),
            ),
        )

        def caption_text(_page: Page, region: LayoutRegion) -> str:
            if region.region_id == "previous-caption":
                return "Figure 6. Previous-page local figure"
            return "Figure 7. Current-page local figure"

        task = build_cross_page_caption_task(
            self.document,
            layouts,
            caption_text=caption_text,
        )
        self.assertEqual(task["pairs"], [])

    def test_explicit_continuation_label_keeps_panel_chain_open(self):
        previous_caption = LayoutRegion(
            "previous-caption",
            NormalizedBBox(0.05, 0.82, 0.9, 0.08),
            "text",
            "caption",
            2,
        )
        current_figure = LayoutRegion(
            "local-figure",
            NormalizedBBox(0.05, 0.05, 0.9, 0.2),
            "visual",
            "figure",
            1,
        )
        current_caption = LayoutRegion(
            "caption",
            NormalizedBBox(0.05, 0.27, 0.9, 0.1),
            "text",
            "caption",
            2,
            source_element_ids=("caption",),
        )
        layouts = (
            FinalLayout(
                source_sha256=SHA,
                page=self.layouts[0].page,
                regions=(self.layouts[0].regions[0], previous_caption),
            ),
            FinalLayout(
                source_sha256=SHA,
                page=self.layouts[1].page,
                regions=(current_figure, current_caption),
            ),
        )

        def caption_text(_page: Page, region: LayoutRegion) -> str:
            if region.region_id == "previous-caption":
                return "Figure 7. Cont."
            return "Figure 7. Continued panels"

        task = build_cross_page_caption_task(
            self.document,
            layouts,
            caption_text=caption_text,
        )
        self.assertEqual(len(task["pairs"]), 1)
        self.assertIn(
            "cross_page_panel_continuity",
            task["pairs"][0]["signals"],
        )

    def test_review_compiles_to_explicit_binding_and_overrides_heuristic(self):
        review = self._review()
        bindings, rejected = compile_cross_page_caption_review(
            review,
            task=self.task,
        )
        by_caption, _, summary = bind_caption_regions(
            self.document,
            self.layouts,
            caption_text=_text,
            reviewed_bindings=bindings,
            rejected_caption_keys=rejected,
        )
        binding = by_caption[(1, "caption")]
        self.assertEqual(binding.visual_region_id, "figure")
        self.assertEqual(binding.method, "reviewed_cross_page_relation")
        self.assertEqual(summary["reviewed_cross_page_binding_count"], 1)

    def test_explicit_rejection_blocks_geometry_guess(self):
        bindings, rejected = compile_cross_page_caption_review(
            self._review(reject=True),
            task=self.task,
        )
        by_caption, _, summary = bind_caption_regions(
            self.document,
            self.layouts,
            caption_text=_text,
            reviewed_bindings=bindings,
            rejected_caption_keys=rejected,
        )
        self.assertNotIn((1, "caption"), by_caption)
        self.assertEqual(summary["reviewed_cross_page_rejection_count"], 1)

    def test_review_rejects_unknown_visual_ref(self):
        review = deepcopy(self._review())
        review["bindings"][0]["visual_ref"] = "p0001:invented"
        with self.assertRaises(ContractValidationError):
            validate_cross_page_caption_review(review, self.task)

    def test_issue_routing_marks_both_pages_for_cross_page_scope(self):
        caption = self.document.pages[1].elements[0]
        first_task = LayoutTask(
            source_sha256=SHA,
            page=LayoutPage.from_page(self.document.pages[0]),
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=(
                LayoutCandidate(
                    "C001",
                    NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                    element_kinds=("raster",),
                    features={"raster_evidence": True},
                ),
            ),
        )
        second_task = LayoutTask(
            source_sha256=SHA,
            page=LayoutPage.from_page(self.document.pages[1]),
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=(
                LayoutCandidate(
                    "C001",
                    NormalizedBBox(0.05, 0.05, 0.9, 0.1),
                    source_element_ids=(caption.element_id,),
                    element_kinds=("text",),
                    features={"starts_with_figure": True},
                ),
            ),
        )
        plan = plan_issue_routing(
            self.document,
            (first_task, second_task),
        ).to_dict()
        issues = [
            item
            for item in plan["issues"]
            if item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
        ]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["page_index"], 1)
        self.assertEqual(issues[0]["scope"]["related_page_indices"], [0])
        self.assertIn(issues[0]["issue_id"], plan["pages"][0]["issue_ids"])
        self.assertIn(issues[0]["issue_id"], plan["pages"][1]["issue_ids"])

    def test_issue_routing_reconstructs_split_caption_for_panel_chain(self):
        provenance = Provenance("fixture", "native", "fixture")
        caption_prefix = Element(
            "caption-prefix",
            "text",
            1,
            BBox(10, 27, 20, 8),
            provenance,
            text="Figure",
            metadata={"line_group": 1, "line_position": 0},
        )
        caption_suffix = Element(
            "caption-suffix",
            "text",
            1,
            BBox(31, 27, 55, 8),
            provenance,
            text="7. Continued panels",
            metadata={"line_group": 1, "line_position": 1},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (caption_prefix, caption_suffix)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.08, 0.9, 0.84),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.05, 0.9, 0.2),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
                metadata={"raster_evidence": {"region_count": 1}},
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        cross_page = [
            item
            for item in issues
            if item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
        ]
        self.assertEqual(len(cross_page), 1)
        self.assertIn(
            "cross_page_panel_continuity",
            cross_page[0]["signals"],
        )
        self.assertEqual(cross_page[0]["scope"]["candidate_ids"], ["C001"])

        low_residual_tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=tasks[0].page,
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.08, 0.9, 0.84),
                        element_kinds=("raster",),
                        features={
                            "raster_evidence": True,
                            "raster_residual_coverage_max": 0.02,
                        },
                    ),
                ),
            ),
            tasks[1],
        )
        low_residual_issues = plan_issue_routing(
            document,
            low_residual_tasks,
        ).to_dict()["issues"]
        self.assertFalse(
            any(
                item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
                for item in low_residual_issues
            )
        )

    def test_issue_routing_recalls_bottom_isolated_label_after_visual_page(self):
        provenance = Provenance("fixture", "native", "fixture")
        caption = Element(
            "caption-bottom",
            "text",
            1,
            BBox(10, 80, 80, 8),
            provenance,
            text="FIGURE 7",
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (caption,)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                metadata={"raster_evidence": {"region_count": 1}},
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                metadata={"raster_evidence": {"region_count": 0}},
            ),
        )
        plan = plan_issue_routing(document, tasks).to_dict()
        issues = [
            item for item in plan["issues"]
            if item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
        ]
        self.assertEqual(len(issues), 1)
        self.assertIn("previous_page_visual_dominant", issues[0]["signals"])

    def test_issue_routing_ignores_inline_figure_reference_fragment(self):
        provenance = Provenance("fixture", "native", "fixture")
        inline_reference = Element(
            "inline-reference",
            "text",
            1,
            BBox(35, 8, 20, 8),
            provenance,
            text="Figure 4B",
            metadata={"line_group": 2, "line_position": 1},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (inline_reference,)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                metadata={"raster_evidence": {"region_count": 1}},
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        self.assertFalse(
            any(
                item["kind"]
                in {
                    ISSUE_CAPTION_VISUAL_BINDING,
                    ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING,
                }
                for item in issues
            )
        )

    def test_issue_routing_ignores_bare_figure_panel_label(self):
        provenance = Provenance("fixture", "native", "fixture")
        panel_label = Element(
            "panel-label",
            "text",
            1,
            BBox(10, 8, 20, 8),
            provenance,
            text="Figure 1A",
            metadata={"line_group": 2, "line_position": 0},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (panel_label,)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                metadata={"raster_evidence": {"region_count": 1}},
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        self.assertFalse(
            any(
                item["kind"]
                in {
                    ISSUE_CAPTION_VISUAL_BINDING,
                    ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING,
                }
                for item in issues
            )
        )

    def test_issue_routing_keeps_arrow_prefixed_split_caption(self):
        provenance = Provenance("fixture", "native", "fixture")
        arrow = Element(
            "arrow",
            "text",
            1,
            BBox(3, 6, 5, 8),
            provenance,
            text="◀",
            metadata={"line_group": 0, "line_position": 0},
        )
        caption = Element(
            "caption",
            "text",
            1,
            BBox(10, 6, 30, 8),
            provenance,
            text="Fig. 5",
            metadata={"line_group": 0, "line_position": 1},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (arrow, caption)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        cross_page = [
            item
            for item in issues
            if item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
        ]
        self.assertEqual(len(cross_page), 1)
        self.assertIn(
            "caption_page_explicit_previous_page_marker",
            cross_page[0]["signals"],
        )

    def test_issue_routing_needs_directional_evidence_below_page_top(self):
        provenance = Provenance("fixture", "native", "fixture")
        local_caption = Element(
            "local-caption",
            "text",
            1,
            BBox(10, 27, 80, 8),
            provenance,
            text="Figure 1. Local plot",
            metadata={"line_group": 3, "line_position": 0},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (local_caption,)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        self.assertFalse(
            any(
                item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
                for item in issues
            )
        )

    def test_issue_routing_prefers_current_visual_over_dominant_previous_page(self):
        provenance = Provenance("fixture", "native", "fixture")
        caption = Element(
            "caption",
            "text",
            1,
            BBox(10, 8, 80, 8),
            provenance,
            text="Figure 2. Current-page result",
            metadata={"line_group": 1, "line_position": 0},
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, ()),
                Page(1, 100, 100, 0, (caption,)),
            ),
        )
        tasks = (
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[0]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
                metadata={"raster_evidence": {"region_count": 1}},
            ),
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(document.pages[1]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(
                    LayoutCandidate(
                        "C001",
                        NormalizedBBox(0.05, 0.55, 0.9, 0.4),
                        element_kinds=("raster",),
                        features={"raster_evidence": True},
                    ),
                ),
            ),
        )
        issues = plan_issue_routing(document, tasks).to_dict()["issues"]
        self.assertFalse(
            any(
                item["kind"] == ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING
                for item in issues
            )
        )

    def test_layout_writer_projects_reviewed_cross_page_relation(self):
        provenance = Provenance("fixture", "native", "fixture")
        title = Element(
            "title", "text", 0, BBox(10, 5, 80, 6), provenance,
            text="Cross-page Fixture",
            metadata={"font_size": 18.0},
        )
        drawing = Element(
            "drawing", "vector", 0, BBox(5, 20, 90, 74), provenance
        )
        caption = Element(
            "caption", "text", 1, BBox(5, 5, 90, 10), provenance,
            text="Figure 7. Cross-page result",
        )
        document = PhysicalDocument(
            SHA,
            "fixture",
            "1",
            (
                Page(0, 100, 100, 0, (title, drawing)),
                Page(1, 100, 100, 0, (caption,)),
            ),
            metadata={"title": "Cross-page Fixture"},
        )
        tasks = tuple(
            LayoutTask(
                source_sha256=SHA,
                page=LayoutPage.from_page(page),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                metadata={
                    "review_mode": "visual-direct",
                    "analysis_roi": {
                        "bbox": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                        "source": "confirmed:fixture",
                    },
                },
            )
            for page in document.pages
        )
        heading_box = NormalizedBBox(0.05, 0.03, 0.9, 0.1)
        figure_box = NormalizedBBox(0.05, 0.18, 0.9, 0.76)
        caption_box = NormalizedBBox(0.05, 0.03, 0.9, 0.14)
        layouts = (
            FinalLayout(
                source_sha256=SHA,
                page=tasks[0].page,
                reviewer="fixture",
                prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
                regions=(
                    LayoutRegion("heading", heading_box, "text", "heading", 1),
                    LayoutRegion("figure", figure_box, "visual", "figure", 2),
                ),
                actions=(
                    LayoutAction("add-heading", "add", result_region_ids=("heading",), bbox=heading_box),
                    LayoutAction("add-figure", "add", result_region_ids=("figure",), bbox=figure_box),
                ),
            ),
            FinalLayout(
                source_sha256=SHA,
                page=tasks[1].page,
                reviewer="fixture",
                prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
                regions=(
                    LayoutRegion("caption", caption_box, "text", "caption", 1),
                ),
                actions=(
                    LayoutAction("add-caption", "add", result_region_ids=("caption",), bbox=caption_box),
                ),
            ),
        )
        materialized = tuple(
            materialize_layout_sources(layout, task, page)
            for layout, task, page in zip(
                layouts, tasks, document.pages, strict=True
            )
        )
        task_value = build_cross_page_caption_task(
            document,
            materialized,
            caption_text=lambda page, region: (
                "Figure 7. Cross-page result"
                if page.page_index == 1 and region.role == "caption"
                else ""
            ),
        )
        pair = task_value["pairs"][0]
        review_value = {
            "contract_version": CROSS_PAGE_CAPTION_REVIEW_VERSION,
            "source_sha256": SHA,
            "task_sha256": cross_page_caption_task_sha256(task_value),
            "reviewer": "fixture-reviewer",
            "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
            "bindings": [
                {
                    "caption_ref": pair["caption"]["caption_ref"],
                    "visual_ref": pair["visual_candidates"][0]["visual_ref"],
                    "confidence": 0.96,
                }
            ],
            "rejected_caption_refs": [],
            "warnings": [],
        }

        class FakeRenderer:
            def render_region(self, source, request, *, expected_source_sha256):
                data = b"project-authored-cross-page-image"
                return RegionRenderResult(
                    figure_id=request.figure_id,
                    data=data,
                    width_px=100,
                    height_px=80,
                    sha256=hashlib.sha256(data).hexdigest(),
                    pixel_variance=10.0,
                    page_area_ratio=0.5,
                    page_rotation=0,
                    renderer_version="fixture",
                    source_sha256=expected_source_sha256,
                    bbox=request.bbox,
                    scale=request.scale,
                    dpi=request.dpi,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            output.mkdir()
            review_root = root / "review"
            review_root.mkdir()
            source = root / "source.pdf"
            source.write_bytes(b"fixture-pdf")
            (review_root / "cross-page-caption-task.json").write_text(
                canonical_cross_page_caption_task_json(task_value),
                encoding="utf-8",
            )
            (review_root / "cross-page-caption-review.json").write_text(
                canonical_cross_page_caption_review_json(
                    review_value,
                    task=task_value,
                ),
                encoding="utf-8",
            )
            result = write_layout_outputs(
                root=output,
                source=source,
                document=document,
                assets=(),
                backend_warnings=(),
                tasks=tasks,
                layouts=layouts,
                region_renderer=FakeRenderer(),
                evidence_level="minimal",
                review_root=review_root,
            )
            article_model = json.loads(
                (output / "_paperwright/article-model.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [item["type"] for item in article_model["relations"]].count(
                    "caption-of"
                ),
                1,
            )
            self.assertEqual(
                result.manifest["images"][0]["caption_binding"]["method"],
                "reviewed_cross_page_relation",
            )


if __name__ == "__main__":
    unittest.main()
