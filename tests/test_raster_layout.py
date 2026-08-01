from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from paper2md.models import BBox, Element, Page, Provenance
from paper2md.models import PhysicalDocument
from paper2md.layout_candidates import (
    generate_layout_tasks,
    propose_content_rois,
)
from paper2md.layout_models import LayoutTask
from paper2md.raster_layout import (
    RasterLayoutConfig,
    analyze_page_raster,
    render_raster_overlay,
)


def _text(
    element_id: str,
    bbox: BBox,
    value: str,
    *,
    page_index: int = 0,
) -> Element:
    return Element(
        element_id=element_id,
        kind="text",
        page_index=page_index,
        bbox=bbox,
        text=value,
        provenance=Provenance(
            backend="fixture",
            method="fixture",
            source_ref=element_id,
            confidence=1.0,
        ),
    )


class RasterLayoutTests(unittest.TestCase):
    def test_text_mask_removes_body_but_keeps_visual_region(self):
        page = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _text("body-left", BBox(50, 50, 220, 180), "body"),
                _text("body-right", BBox(330, 50, 220, 180), "body"),
                _text("figure-label", BBox(80, 390, 80, 20), "A label"),
                _text("caption", BBox(50, 680, 500, 45), "Figure 1."),
            ),
        )
        preview = Image.new("RGB", (600, 800), (250, 250, 248))
        draw = ImageDraw.Draw(preview)
        draw.rectangle((55, 60, 265, 220), fill=(20, 20, 20))
        draw.rectangle((335, 60, 545, 220), fill=(20, 20, 20))
        draw.rectangle((55, 685, 545, 720), fill=(20, 20, 20))
        draw.rectangle((60, 340, 540, 650), outline=(15, 15, 15), width=5)
        for x in range(90, 530, 45):
            draw.line((x, 360, x, 630), fill=(35, 90, 180), width=4)
        draw.line((70, 610, 525, 380), fill=(190, 40, 45), width=8)
        draw.rectangle((82, 392, 155, 408), fill=(10, 10, 10))

        result = analyze_page_raster(preview, page)

        self.assertGreater(result.analysis.ink_coverage, 0)
        self.assertGreater(result.analysis.text_mask_coverage, 0)
        self.assertLess(
            result.analysis.residual_coverage,
            result.analysis.ink_coverage,
        )
        self.assertTrue(result.analysis.regions)
        figure = max(
            result.analysis.regions,
            key=lambda item: item.page_area_ratio,
        )
        self.assertLess(figure.bbox.x, 0.15)
        self.assertGreater(figure.bbox.right, 0.85)
        self.assertLess(figure.bbox.y, 0.46)
        self.assertGreater(figure.bbox.bottom, 0.78)

    def test_text_only_page_has_no_visual_candidate(self):
        page = Page(
            page_index=0,
            width=300,
            height=400,
            rotation=0,
            elements=(
                _text("body", BBox(30, 40, 240, 280), "body"),
            ),
        )
        preview = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(preview).rectangle((35, 45, 265, 315), fill="black")

        result = analyze_page_raster(preview, page)

        self.assertEqual(result.analysis.residual_coverage, 0)
        self.assertEqual(result.analysis.regions, ())

    def test_analysis_is_deterministic_and_masks_are_distinct(self):
        page = Page(
            page_index=0,
            width=200,
            height=200,
            rotation=0,
            elements=(_text("label", BBox(20, 20, 40, 20), "label"),),
        )
        preview = Image.new("RGB", (200, 200), "white")
        draw = ImageDraw.Draw(preview)
        draw.rectangle((20, 20, 60, 40), fill="black")
        draw.ellipse((50, 80, 150, 170), fill=(20, 100, 180))
        config = RasterLayoutConfig(grid_cell_px=3)

        first = analyze_page_raster(preview, page, config=config)
        second = analyze_page_raster(preview, page, config=config)

        self.assertEqual(
            first.analysis.canonical_json(),
            second.analysis.canonical_json(),
        )
        self.assertNotEqual(
            first.analysis.ink_mask_sha256,
            first.analysis.residual_mask_sha256,
        )
        self.assertEqual(first.ink_mask.mode, "L")
        self.assertEqual(first.text_mask.size, preview.size)

        overlay = render_raster_overlay(preview, first.analysis)
        self.assertEqual(overlay.mode, "RGB")
        self.assertEqual(overlay.size, preview.size)
        self.assertEqual(preview.getpixel((0, 0)), (255, 255, 255))

    def test_raster_evidence_generates_v2_task_without_fake_object_ids(self):
        page = Page(
            page_index=0,
            width=300,
            height=400,
            rotation=0,
            elements=(
                _text("body", BBox(30, 75, 240, 20), "body"),
                _text("panel", BBox(50, 210, 30, 10), "A"),
                _text("caption", BBox(30, 330, 240, 25), "Figure 1."),
            ),
        )
        document = PhysicalDocument(
            source_sha256="a" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        preview = Image.new("RGB", (300, 400), "white")
        draw = ImageDraw.Draw(preview)
        draw.rectangle((215, 8, 285, 24), fill="black")
        draw.rectangle((32, 78, 268, 92), fill="black")
        draw.rectangle((35, 190, 265, 315), outline="black", width=5)
        draw.line((45, 300, 250, 205), fill=(20, 80, 180), width=7)
        draw.rectangle((50, 210, 80, 220), fill="black")
        draw.rectangle((32, 332, 268, 352), fill="black")
        raster = analyze_page_raster(preview, page).analysis

        rois = propose_content_rois(
            document,
            raster_analyses={0: raster},
        )
        tasks = generate_layout_tasks(
            document,
            content_rois=rois,
            content_roi_source="raster_rule_proposed",
            raster_analyses={0: raster},
        )

        self.assertGreater(rois[0].y, 0.03)
        task = tasks[0]
        self.assertEqual(task.contract_version, "paper2md-layout-task-v0.2")
        self.assertEqual(
            task.candidate_generator_version,
            "paper2md-whitespace-raster-candidates-v0.1",
        )
        self.assertEqual(
            task.metadata["raster_evidence"]["ink_mask_sha256"],
            raster.ink_mask_sha256,
        )
        raster_candidates = [
            item for item in task.candidates if "raster" in item.element_kinds
        ]
        self.assertTrue(raster_candidates)
        self.assertTrue(
            all(
                candidate.element_kinds == ("raster",)
                for candidate in raster_candidates
            )
        )
        self.assertTrue(
            all(candidate.bbox.y > 0.10 for candidate in raster_candidates)
        )
        self.assertTrue(task.metadata["raster_suppressed_element_ids"])
        self.assertTrue(
            all(
                not source_id.startswith("raster-")
                for candidate in raster_candidates
                for source_id in candidate.source_element_ids
            )
        )
        self.assertEqual(
            LayoutTask.from_dict(task.to_dict()).canonical_json(),
            task.canonical_json(),
        )
        legacy_task = generate_layout_tasks(document)[0]
        self.assertEqual(
            legacy_task.contract_version,
            "paper2md-layout-task-v0.1",
        )
        self.assertNotIn("raster_evidence", legacy_task.metadata)
        self.assertNotIn(
            "raster_suppressed_element_ids",
            legacy_task.metadata,
        )


if __name__ == "__main__":
    unittest.main()
