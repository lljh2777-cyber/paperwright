import tempfile
import unittest
from pathlib import Path

from paperwright.api import PaperWright
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.layout_candidates import generate_layout_tasks
from paperwright.layout_models import NormalizedBBox
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.raster_layout import RasterPageAnalysis, RasterVisualRegion

from pdf_fixture_factory import create_born_digital_fixture


def _text(
    element_id: str,
    page_index: int,
    bbox: BBox,
    value: str,
    line_group: int,
    *,
    size: float = 10.0,
) -> Element:
    return Element(
        element_id=element_id,
        kind="text",
        page_index=page_index,
        bbox=bbox,
        text=value,
        provenance=Provenance(
            "fixture",
            "native_text",
            f"fixture:{element_id}",
            confidence=1.0,
        ),
        metadata={
            "line_group": line_group,
            "line_position": 0,
            "native_line_text": value,
            "font_name": "FixtureSans",
            "font_size": size,
        },
    )


def _image(
    element_id: str,
    page_index: int,
    bbox: BBox,
) -> Element:
    return Element(
        element_id=element_id,
        kind="image",
        page_index=page_index,
        bbox=bbox,
        provenance=Provenance(
            "fixture",
            "native_image",
            f"fixture:{element_id}",
            confidence=1.0,
        ),
    )


def _article_page(page_index: int) -> Page:
    line = page_index * 100
    elements = [
        _text(
            f"p{page_index}-header",
            page_index,
            BBox(50, 10, 160, 10),
            "RESEARCH ARTICLE",
            line,
            size=8,
        ),
        _text(
            f"p{page_index}-heading",
            page_index,
            BBox(50, 35, 500, 12),
            "A spanning section heading",
            line + 1,
            size=13,
        ),
    ]
    for offset, y in enumerate((65, 80, 95)):
        elements.extend(
            (
                _text(
                    f"p{page_index}-left-{offset}",
                    page_index,
                    BBox(50, y, 225, 10),
                    f"Left column line {offset}",
                    line + 2 + offset,
                ),
                _text(
                    f"p{page_index}-right-{offset}",
                    page_index,
                    BBox(325, y, 225, 10),
                    f"Right column line {offset}",
                    line + 5 + offset,
                ),
            )
        )
    elements.extend(
        (
            _image(
                f"p{page_index}-figure",
                page_index,
                BBox(50, 180, 500, 300),
            ),
            _text(
                f"p{page_index}-caption",
                page_index,
                BBox(50, 500, 500, 20),
                "Fig. 1. A complete visual region.",
                line + 8,
                size=8,
            ),
            _text(
                f"p{page_index}-footer",
                page_index,
                BBox(500, 780, 50, 10),
                f"{page_index + 1} of 2",
                line + 9,
                size=8,
            ),
        )
    )
    return Page(
        page_index=page_index,
        width=600,
        height=800,
        rotation=0,
        elements=tuple(elements),
    )


def _article_document() -> PhysicalDocument:
    return PhysicalDocument(
        source_sha256="c" * 64,
        backend="fixture",
        backend_version="1",
        pages=(_article_page(0), _article_page(1)),
    )


class LayoutStageBTests(unittest.TestCase):
    def test_spanning_heading_two_columns_figure_and_caption_are_separate(self):
        task = generate_layout_tasks(_article_document())[0]
        by_element = {
            element_id: candidate
            for candidate in task.candidates
            for element_id in candidate.source_element_ids
        }

        self.assertIsNot(
            by_element["p0-heading"],
            by_element["p0-left-0"],
        )
        self.assertIs(
            by_element["p0-left-0"],
            by_element["p0-left-2"],
        )
        self.assertIs(
            by_element["p0-right-0"],
            by_element["p0-right-2"],
        )
        self.assertIsNot(
            by_element["p0-left-0"],
            by_element["p0-right-0"],
        )
        self.assertIsNot(
            by_element["p0-figure"],
            by_element["p0-caption"],
        )
        self.assertEqual(
            by_element["p0-figure"].features["native_text_coverage"],
            0.0,
        )
        self.assertTrue(
            by_element["p0-caption"].features["starts_with_figure"]
        )

    def test_repeated_furniture_is_excluded_before_candidate_generation(self):
        tasks = generate_layout_tasks(_article_document())
        for page_index, task in enumerate(tasks):
            candidate_element_ids = {
                element_id
                for candidate in task.candidates
                for element_id in candidate.source_element_ids
            }
            self.assertNotIn(f"p{page_index}-header", candidate_element_ids)
            self.assertNotIn(f"p{page_index}-footer", candidate_element_ids)
            self.assertIn(
                f"p{page_index}-header",
                task.metadata["excluded_element_ids"],
            )
            self.assertEqual(
                task.metadata["analysis_roi"]["coordinate_system"],
                "top-left/original-page-normalized/y-down",
            )
            self.assertFalse(task.metadata["analysis_roi"]["destructive_crop"])

    def test_confirmed_roi_limits_candidates_without_changing_page_coordinates(self):
        document = _article_document()
        roi = NormalizedBBox(0.05, 0.10, 0.90, 0.80)
        tasks = generate_layout_tasks(
            document,
            content_rois={page.page_index: roi for page in document.pages},
            content_roi_source="confirmed:test",
        )
        for task in tasks:
            self.assertEqual(task.metadata["analysis_roi"]["bbox"], roi.to_dict())
            self.assertEqual(
                task.page.coordinate_system,
                "top-left/pdf-point/y-down",
            )
            for candidate in task.candidates:
                self.assertGreaterEqual(candidate.bbox.x, roi.x)
                self.assertGreaterEqual(candidate.bbox.y, roi.y)
                self.assertLessEqual(candidate.bbox.right, roi.right + 1e-9)
                self.assertLessEqual(candidate.bbox.bottom, roi.bottom + 1e-9)

    def test_rule_roi_removes_sparse_edge_header_and_text_footer(self):
        page = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _text(
                    "journal-badge",
                    0,
                    BBox(470, 12, 80, 16),
                    "LETTER RESEARCH",
                    0,
                    size=8,
                ),
                Element(
                    element_id="journal-rule",
                    kind="vector",
                    page_index=0,
                    bbox=BBox(50, 30, 500, 2),
                    provenance=Provenance(
                        "fixture",
                        "native_vector",
                        "fixture:journal-rule",
                        confidence=1.0,
                    ),
                ),
                _text(
                    "body-top",
                    0,
                    BBox(50, 70, 500, 12),
                    "Body starts here",
                    1,
                ),
                _text(
                    "body-bottom",
                    0,
                    BBox(50, 700, 500, 12),
                    "Body ends here",
                    2,
                ),
                _text(
                    "date-folio",
                    0,
                    BBox(400, 775, 150, 8),
                    "17 MAY 2012 | VOL 485",
                    3,
                    size=8,
                ),
            ),
        )
        document = PhysicalDocument(
            source_sha256="e" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        task = generate_layout_tasks(document)[0]
        candidate_ids = {
            element_id
            for candidate in task.candidates
            for element_id in candidate.source_element_ids
        }
        self.assertNotIn("journal-badge", candidate_ids)
        self.assertNotIn("journal-rule", candidate_ids)
        self.assertNotIn("date-folio", candidate_ids)
        self.assertIn("body-top", candidate_ids)
        self.assertIn("body-bottom", candidate_ids)
        roi = task.metadata["analysis_roi"]["bbox"]
        self.assertGreater(roi["y"], 0.03)
        self.assertLess(roi["y"] + roi["height"], 0.95)

    def test_vertical_and_horizontal_separators_are_recorded(self):
        task = generate_layout_tasks(_article_document())[0]
        orientations = {item.orientation for item in task.separators}
        self.assertEqual(orientations, {"horizontal", "vertical"})
        candidate_ids = {item.candidate_id for item in task.candidates}
        for separator in task.separators:
            self.assertTrue(
                set(separator.adjacent_candidate_ids) <= candidate_ids
            )
        self.assertLessEqual(len(task.separators), 2 * len(task.candidates))

    def test_multi_panel_raster_fragments_form_one_auditable_candidate(self):
        page = Page(
            0,
            600,
            800,
            0,
            (
                _text(
                    "caption",
                    0,
                    BBox(40, 455, 520, 40),
                    "Fig. 1 | Multi-panel benchmark.",
                    1,
                ),
            ),
        )
        document = PhysicalDocument(
            source_sha256="9" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        regions = tuple(
            RasterVisualRegion(
                region_id=f"R{index + 1:03d}",
                bbox=NormalizedBBox(x, y, 0.25, 0.22),
                page_area_ratio=0.055,
                ink_coverage=0.5,
                residual_coverage=0.4,
                text_mask_coverage=0.1,
            )
            for index, (x, y) in enumerate(
                (
                    (0.08, 0.08),
                    (0.375, 0.08),
                    (0.67, 0.08),
                    (0.08, 0.325),
                    (0.375, 0.325),
                    (0.67, 0.325),
                )
            )
        )
        raster = RasterPageAnalysis(
            page_index=0,
            preview_width=600,
            preview_height=800,
            background_rgb=(255, 255, 255),
            text_padding_px=1,
            ink_coverage=0.3,
            text_mask_coverage=0.05,
            residual_coverage=0.25,
            ink_mask_sha256="a" * 64,
            text_mask_sha256="b" * 64,
            residual_mask_sha256="c" * 64,
            regions=regions,
        )

        task = generate_layout_tasks(
            document,
            content_rois={0: NormalizedBBox(0.05, 0.05, 0.90, 0.90)},
            raster_analyses={0: raster},
        )[0]

        raster_candidates = [
            item for item in task.candidates if "raster" in item.element_kinds
        ]
        self.assertEqual(len(raster_candidates), 1)
        self.assertEqual(
            raster_candidates[0].features["raster_region_count"],
            6,
        )
        self.assertEqual(
            task.metadata["raster_candidate_groups"][0]["component_ids"],
            [f"R{index:03d}" for index in range(1, 7)],
        )
        caption_candidate = next(
            item
            for item in task.candidates
            if item.features.get("high_confidence_caption_kind") == "figure"
        )
        hint = task.metadata["semantic_review_hints"][0]
        self.assertEqual(hint["visual_role"], "figure")
        self.assertEqual(
            hint["caption_candidate_ids"],
            [caption_candidate.candidate_id],
        )
        self.assertEqual(
            hint["visual_candidate_ids"],
            [raster_candidates[0].candidate_id],
        )

    def test_separate_raster_figures_remain_separate(self):
        page = Page(0, 600, 800, 0, ())
        document = PhysicalDocument(
            source_sha256="8" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        regions = tuple(
            RasterVisualRegion(
                region_id=f"R{index + 1:03d}",
                bbox=NormalizedBBox(0.1, y, 0.8, 0.18),
                page_area_ratio=0.144,
                ink_coverage=0.5,
                residual_coverage=0.4,
                text_mask_coverage=0.1,
            )
            for index, y in enumerate((0.10, 0.55))
        )
        raster = RasterPageAnalysis(
            0, 600, 800, (255, 255, 255), 1,
            0.3, 0.0, 0.3,
            "d" * 64, "e" * 64, "f" * 64,
            regions,
        )

        task = generate_layout_tasks(
            document,
            content_rois={0: NormalizedBBox(0.05, 0.05, 0.90, 0.90)},
            raster_analyses={0: raster},
        )[0]

        self.assertEqual(
            sum("raster" in item.element_kinds for item in task.candidates),
            2,
        )

    def test_wide_shallow_edge_rules_do_not_join_a_figure(self):
        page = Page(0, 600, 800, 0, ())
        document = PhysicalDocument(
            source_sha256="7" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        regions = (
            RasterVisualRegion(
                "header-rule",
                NormalizedBBox(0.05, 0.04, 0.90, 0.02),
                0.018,
                0.10,
                0.06,
                0.10,
            ),
            RasterVisualRegion(
                "figure",
                NormalizedBBox(0.05, 0.075, 0.90, 0.65),
                0.585,
                0.40,
                0.35,
                0.10,
            ),
            RasterVisualRegion(
                "footer-rule",
                NormalizedBBox(0.05, 0.945, 0.90, 0.02),
                0.018,
                0.08,
                0.04,
                0.08,
            ),
        )
        raster = RasterPageAnalysis(
            0, 600, 800, (255, 255, 255), 1,
            0.3, 0.0, 0.3,
            "1" * 64, "2" * 64, "3" * 64,
            regions,
        )

        task = generate_layout_tasks(
            document,
            content_rois={0: NormalizedBBox(0.01, 0.01, 0.98, 0.98)},
            raster_analyses={0: raster},
        )[0]

        raster_candidates = [
            item for item in task.candidates if "raster" in item.element_kinds
        ]
        self.assertEqual(len(raster_candidates), 1)
        self.assertAlmostEqual(raster_candidates[0].bbox.y, 0.075)
        self.assertEqual(
            task.metadata["raster_candidate_groups"][0]["component_ids"],
            ["figure"],
        )

    def test_narrow_low_occupancy_gutter_survives_sparse_crossing_line(self):
        elements = []
        for offset, y in enumerate(range(60, 180, 12)):
            elements.extend(
                (
                    _text(
                        f"left-{offset}",
                        0,
                        BBox(40, y, 250, 9),
                        f"Left body line {offset}",
                        offset,
                    ),
                    _text(
                        f"right-{offset}",
                        0,
                        BBox(300, y, 250, 9),
                        f"Right body line {offset}",
                        offset + 20,
                    ),
                )
            )
        elements.append(
            _text(
                "sparse-crossing",
                0,
                BBox(260, 110, 70, 6),
                "one crossing",
                99,
                size=6,
            )
        )
        page = Page(0, 600, 800, 0, tuple(elements))
        document = PhysicalDocument(
            source_sha256="e" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        task = generate_layout_tasks(document)[0]
        by_element = {
            element_id: candidate
            for candidate in task.candidates
            for element_id in candidate.source_element_ids
        }
        self.assertIsNot(by_element["left-0"], by_element["right-0"])

    def test_spanning_footer_does_not_widen_either_column_candidate(self):
        elements = []
        for offset, y in enumerate(range(60, 180, 12)):
            elements.extend(
                (
                    _text(
                        f"left-{offset}",
                        0,
                        BBox(40, y, 250, 9),
                        f"Left body line {offset}",
                        offset,
                    ),
                    _text(
                        f"right-{offset}",
                        0,
                        BBox(310, y, 250, 9),
                        f"Right body line {offset}",
                        offset + 20,
                    ),
                )
            )
        elements.append(
            _text(
                "spanning-footer",
                0,
                BBox(40, 188, 520, 7),
                "A sparse full-width affiliation or footer line",
                99,
                size=7,
            )
        )
        page = Page(0, 600, 800, 0, tuple(elements))
        document = PhysicalDocument(
            source_sha256="f" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )

        task = generate_layout_tasks(document)[0]
        by_element = {
            element_id: candidate
            for candidate in task.candidates
            for element_id in candidate.source_element_ids
        }
        left = by_element["left-0"]
        right = by_element["right-0"]
        footer = by_element["spanning-footer"]

        self.assertIsNot(left, right)
        self.assertIsNot(left, footer)
        self.assertIsNot(right, footer)
        self.assertLess(left.bbox.width, 0.5)
        self.assertLess(right.bbox.width, 0.5)
        self.assertGreater(footer.bbox.width, 0.8)

    def test_scanned_page_uses_unknown_native_text_coverage_without_ocr(self):
        page = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(_image("scan", 0, BBox(0, 0, 600, 800)),),
        )
        document = PhysicalDocument(
            source_sha256="d" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        task = generate_layout_tasks(document)[0]
        self.assertEqual(len(task.candidates), 1)
        self.assertFalse(
            task.candidates[0].features["page_native_text_available"]
        )
        self.assertIsNone(
            task.candidates[0].features["native_text_coverage"]
        )
        self.assertFalse(task.metadata["ocr_used"])

    def test_candidate_generation_is_deterministic(self):
        document = _article_document()
        first = generate_layout_tasks(document)
        second = generate_layout_tasks(document)
        self.assertEqual(
            [item.canonical_json() for item in first],
            [item.canonical_json() for item in second],
        )

    def test_real_pdfium_physical_document_generates_valid_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            product = PaperWright(PaperWrightConfig(workspace_root=root))
            product.register_backend("pdfium", PDFiumBackend())
            document = product.extract_physical_document(
                source,
                root / "unused-output",
            )
            tasks = generate_layout_tasks(document)
            self.assertEqual(len(tasks), len(document.pages))
            self.assertTrue(all(task.candidates for task in tasks))
            self.assertTrue(
                all(task.metadata["ocr_used"] is False for task in tasks)
            )


if __name__ == "__main__":
    unittest.main()
