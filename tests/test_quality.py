import tempfile
import unittest
from pathlib import Path

from paperwright.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutCandidate,
    LayoutPage,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.quality import (
    analyze_image_links,
    analyze_layout_elements,
    analyze_manifest_inventory,
    analyze_markdown_exclusions,
    analyze_markdown_text,
    analyze_native_object_diagnostics,
    analyze_semantic_layout,
    analyze_title,
    analyze_word_spacing,
)


class QualityValidationTests(unittest.TestCase):
    def test_semantic_layout_rejects_caption_text_in_body(self):
        paragraphs = [
            {
                "page_index": 4,
                "region_id": "R21",
                "paragraph_index": 0,
                "role": "body",
                "text": "Fig. 3 | Benchmark of spatial domains.",
            }
        ]

        result = analyze_semantic_layout(
            paragraphs,
            markdown_text={"findings": []},
            figure_label_leakage={"findings": []},
            runtime_warnings=(),
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["caption_like_non_caption_count"], 1)

    def test_semantic_layout_combines_independent_page_signals(self):
        result = analyze_semantic_layout(
            (),
            markdown_text={
                "findings": [
                    {"code": "short_body_fragment", "page": 5}
                ]
            },
            figure_label_leakage={"findings": [{"page": 5}]},
            runtime_warnings=(),
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["compound_fragmentation_page_count"], 1)

    def test_markdown_exclusions_remain_auditable(self):
        element = Element(
            "dingbat",
            "text",
            0,
            BBox(90, 20, 5, 5),
            Provenance("fixture", "native", "fixture"),
            text="\uf0a3",
            metadata={
                "markdown_excluded_reason": (
                    "decorative_line_end_private_use_dingbat"
                )
            },
        )
        document = PhysicalDocument(
            "d" * 64,
            "fixture",
            "1",
            (Page(0, 100, 100, 0, (element,)),),
        )

        result = analyze_markdown_exclusions(document)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual(
            result["findings"][0]["text_codepoints"],
            ["U+F0A3"],
        )

    def test_native_object_diagnostics_separate_safe_and_risky_objects(self):
        page = Page(0, 100, 100, 0, ())
        document = PhysicalDocument(
            "d" * 64,
            "fixture",
            "1",
            (page,),
            metadata={
                "degenerate_object_handling": {
                    "policy_version": "fixture-v1",
                    "counts": {
                        "ignored_degenerate_empty_text": 12,
                        "ignored_degenerate_form_container": 1,
                        "unplaced_degenerate_vector_path": 2,
                    },
                    "pages": [
                        {
                            "page": 1,
                            "counts": {
                                "ignored_degenerate_empty_text": 12,
                                "ignored_degenerate_form_container": 1,
                                "unplaced_degenerate_vector_path": 2,
                            },
                        }
                    ],
                }
            },
        )
        result = analyze_native_object_diagnostics(document)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["ignored_safe_object_count"], 13)
        self.assertEqual(result["unplaced_risk_object_count"], 2)
        self.assertEqual(result["findings"][0]["page"], 1)
        self.assertEqual(result["findings"][0]["count"], 2)

    def test_layout_element_coverage_and_uniqueness_are_reported(self):
        provenance = Provenance("fixture", "fixture", "fixture:quality")
        first = Element(
            "p0000-text-00001",
            "text",
            0,
            BBox(10, 10, 30, 10),
            provenance,
            text="first",
        )
        second = Element(
            "p0000-text-00002",
            "text",
            0,
            BBox(10, 30, 30, 10),
            provenance,
            text="second",
        )
        page = Page(0, 100, 100, 0, (first, second))
        document = PhysicalDocument("a" * 64, "fixture", "1", (page,))
        layout_page = LayoutPage.from_page(page)
        task = LayoutTask(
            source_sha256="a" * 64,
            page=layout_page,
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=(
                LayoutCandidate(
                    "C1",
                    NormalizedBBox(0.1, 0.1, 0.3, 0.3),
                    (first.element_id, second.element_id),
                    ("text",),
                ),
            ),
        )
        layout = FinalLayout(
            source_sha256="a" * 64,
            page=layout_page,
            regions=(
                LayoutRegion(
                    "R1",
                    NormalizedBBox(0.1, 0.1, 0.3, 0.1),
                    "text",
                    "body",
                    1,
                    source_element_ids=(first.element_id,),
                ),
                LayoutRegion(
                    "R2",
                    NormalizedBBox(0.1, 0.1, 0.3, 0.1),
                    "text",
                    "body",
                    2,
                    source_element_ids=(first.element_id,),
                ),
            ),
        )
        result = analyze_layout_elements((task,), (layout,), document)
        self.assertEqual(result["coverage"]["unassigned_count"], 1)
        self.assertEqual(result["uniqueness"]["duplicate_assignment_count"], 1)

    def test_explicitly_discarded_candidate_is_not_unassigned(self):
        provenance = Provenance("fixture", "fixture", "fixture:discard")
        item = Element(
            "p0000-text-00001",
            "text",
            0,
            BBox(10, 10, 30, 10),
            provenance,
            text="footer",
        )
        page = Page(0, 100, 100, 0, (item,))
        document = PhysicalDocument("b" * 64, "fixture", "1", (page,))
        layout_page = LayoutPage.from_page(page)
        task = LayoutTask(
            source_sha256="b" * 64,
            page=layout_page,
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=(
                LayoutCandidate(
                    "C1",
                    NormalizedBBox(0.1, 0.1, 0.3, 0.1),
                    (item.element_id,),
                    ("text",),
                ),
            ),
        )
        layout = FinalLayout(
            source_sha256="b" * 64,
            page=layout_page,
            regions=(),
            actions=(
                LayoutAction(
                    "A1",
                    "discard",
                    source_candidate_ids=("C1",),
                ),
            ),
        )
        result = analyze_layout_elements((task,), (layout,), document)
        self.assertEqual(result["coverage"]["status"], "pass")
        self.assertEqual(result["coverage"]["unassigned_count"], 0)
        self.assertEqual(
            result["coverage"]["intentionally_discarded_text_object_count"],
            1,
        )

    def test_markdown_quality_detects_repetition_fragments_and_figure_labels(self):
        records = [
            {
                "page_index": 0,
                "region_id": "R1",
                "paragraph_index": 0,
                "role": "body",
                "text": "SUPPLE M E NTARY",
                "is_bold": False,
            },
            {
                "page_index": 0,
                "region_id": "R2",
                "paragraph_index": 0,
                "role": "body",
                "text": "the the model",
                "is_bold": False,
            },
            {
                "page_index": 1,
                "region_id": "R3",
                "paragraph_index": 0,
                "role": "body",
                "text": "A B C 10 20 30 40",
                "is_bold": False,
            },
        ]
        result = analyze_markdown_text(records)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["suspected_broken_word_count"], 1)
        self.assertEqual(result["repeated_word_count"], 1)
        self.assertEqual(
            result["figure_label_leakage"]["suspected_count"], 1
        )

    def test_word_spacing_flags_residual_glued_scientific_tokens(self):
        result = analyze_word_spacing(
            [
                {
                    "page_index": 3,
                    "region_id": "R1",
                    "paragraph_index": 0,
                    "text": (
                        "IL10and CD24within cells used 80%training data "
                        "from TCGAand another cohort."
                    ),
                    "reconstruction_events": [],
                }
            ]
        )
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["suspected_missing_space_count"], 1)
        self.assertEqual(
            result["findings"][0]["code"],
            "suspected_missing_word_space",
        )

    def test_word_spacing_audits_geometric_repairs_and_soft_breaks(self):
        result = analyze_word_spacing(
            [
                {
                    "page_index": 3,
                    "region_id": "R1",
                    "paragraph_index": 0,
                    "text": "IL10 and adjacent tumor",
                    "reconstruction_events": [
                        {"code": "inserted_geometric_word_space"},
                        {"code": "collapsed_tight_same_font_fragment_gap"},
                        {
                            "code": "joined_explicit_pdf_soft_break",
                            "before": "tumor | enriched",
                            "after": "tumorenriched",
                        },
                    ],
                }
            ]
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["geometric_space_insertion_count"], 1)
        self.assertEqual(result["geometric_fragment_join_count"], 1)
        self.assertEqual(result["ambiguous_soft_break_join_count"], 1)

    def test_bold_short_heading_is_not_a_short_body_fragment(self):
        result = analyze_markdown_text(
            [
                {
                    "page_index": 0,
                    "region_id": "R1",
                    "paragraph_index": 0,
                    "role": "body",
                    "text": "Materials and methods",
                    "is_bold": True,
                }
            ]
        )
        self.assertEqual(result["short_body_fragment_count"], 0)

    def test_uppercase_short_heading_is_not_a_short_body_fragment(self):
        result = analyze_markdown_text(
            [
                {
                    "page_index": 0,
                    "region_id": "R1",
                    "paragraph_index": 0,
                    "role": "body",
                    "text": "METHODS SUMMARY",
                    "is_bold": False,
                }
            ]
        )
        self.assertEqual(result["short_body_fragment_count"], 0)

    def test_title_and_image_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            images.mkdir()
            (images / "figure.png").write_bytes(b"png")
            article = root / "article.md"
            article.write_text(
                "# Complete title\n\n![Figure](images/figure.png)\n",
                encoding="utf-8",
            )
            self.assertEqual(
                analyze_title("Complete title", article.read_text())["status"],
                "pass",
            )
            self.assertEqual(analyze_image_links(article, images)["status"], "pass")
            inventory = analyze_manifest_inventory(root, [article, images / "figure.png"])
            self.assertEqual(inventory["status"], "pass")

    def test_image_inventory_reports_missing_and_orphaned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            images.mkdir()
            (images / "orphan.png").write_bytes(b"png")
            article = root / "article.md"
            article.write_text("![Missing](images/missing.png)\n", encoding="utf-8")
            result = analyze_image_links(article, images)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["missing"], ["images/missing.png"])
            self.assertEqual(result["orphaned"], ["images/orphan.png"])


if __name__ == "__main__":
    unittest.main()
