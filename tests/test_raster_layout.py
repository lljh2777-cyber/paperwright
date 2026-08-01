from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from paper2md.models import BBox, Element, Page, Provenance
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


if __name__ == "__main__":
    unittest.main()
