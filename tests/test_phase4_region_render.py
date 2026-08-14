from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from paperwright.api import PaperWright
from paperwright.backends import BackendRegistry
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig, RegionRenderPolicy
from paperwright.exceptions import BackendExecutionError
from paperwright.figures import analyze_figures
from paperwright.manifest import validate_manifest
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.region_render import RegionRenderRequest, plan_region_renders

from pdf_fixture_factory import create_region_render_fixture


def _request(
    bbox: BBox = BBox(50, 62, 500, 400),
    **overrides,
) -> RegionRenderRequest:
    values = {
        "figure_id": "fig-p001-001",
        "page_index": 0,
        "bbox": bbox,
        "caption_top": 480.0,
        "caption_id": "caption-1",
        "caption_element_ids": ("caption-text",),
        "caption_text": "Figure 1. Caption",
        "caption_bbox": BBox(50, 480, 300, 12),
        "caption_reason": "fixture_explicit_caption",
        "caption_confidence": 1.0,
        "member_element_ids": ("image-1",),
        "vector_evidence_element_ids": ("vector-1",),
        "vector_evidence_count": 4,
        "vector_evidence_sha256": hashlib.sha256(b"vector-1").hexdigest(),
        "fallback_reason": "fixture_mixed_bitmap_vector",
        "bbox_rule": "fixture_outer_frame",
    }
    values.update(overrides)
    return RegionRenderRequest(**values)


class Phase4RegionRenderTests(unittest.TestCase):
    @staticmethod
    def _registry() -> BackendRegistry:
        registry = BackendRegistry()
        registry.register("pdfium", PDFiumBackend())
        return registry

    def _render(self, *, rotation=0, blank=False, request=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "fixture.pdf"
        meta = create_region_render_fixture(source, rotation=rotation, blank=blank)
        result = PDFiumBackend().render_region(
            source,
            request or _request(),
            expected_source_sha256=meta["sha256"],
        )
        return result

    def test_real_clipped_render_records_coordinates_scale_and_hash(self):
        result = self._render()
        self.assertEqual((result.width_px, result.height_px), (1000, 800))
        self.assertEqual(result.bbox, BBox(50, 62, 500, 400))
        self.assertEqual((result.scale, result.dpi), (2.0, 144.0))
        self.assertGreater(result.pixel_variance, 2.0)
        self.assertEqual(result.sha256, hashlib.sha256(result.data).hexdigest())
        self.assertLessEqual(result.bbox.bottom, 480.0 - 4.0)

    def test_rotation_is_recorded_and_crop_is_nonblank(self):
        result = self._render(
            rotation=90,
            request=_request(
                BBox(50, 1, 500, 280),
                caption_top=400.0,
                caption_bbox=BBox(50, 400, 200, 10),
            ),
        )
        self.assertEqual(result.page_rotation, 90)
        self.assertEqual((result.width_px, result.height_px), (1000, 560))
        self.assertGreater(result.pixel_variance, 2.0)

    def test_scale_changes_pixel_dimensions_deterministically(self):
        result = self._render(
            request=_request(scale=1.5, dpi=108.0),
        )
        self.assertEqual((result.width_px, result.height_px), (750, 600))

    def test_out_of_bounds_bbox_is_rejected(self):
        with self.assertRaisesRegex(BackendExecutionError, "bbox 越界"):
            self._render(request=_request(BBox(500, 62, 150, 300)))

    def test_near_full_page_is_rejected(self):
        with self.assertRaisesRegex(BackendExecutionError, "整页或近整页"):
            self._render(
                request=_request(
                    BBox(1, 1, 610, 700),
                    caption_top=780,
                    caption_bbox=BBox(10, 780, 50, 5),
                )
            )

    def test_blank_region_is_rejected(self):
        with self.assertRaisesRegex(BackendExecutionError, "空白或近恒定"):
            self._render(blank=True)

    def test_pixel_limit_is_hard_rejected(self):
        with self.assertRaisesRegex(BackendExecutionError, "像素上限"):
            self._render(request=_request(max_pixels=100))

    def test_caption_guard_prevents_burn_in(self):
        with self.assertRaisesRegex(BackendExecutionError, "caption guard"):
            self._render(
                request=_request(
                    BBox(50, 62, 500, 403),
                    caption_top=468,
                    caption_bbox=BBox(50, 468, 200, 10),
                )
            )

    def test_two_renders_are_byte_deterministic(self):
        first = self._render()
        second = self._render()
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.data, second.data)

    def test_cross_page_continuation_is_explicitly_rejected(self):
        provenance = Provenance("fixture", "self_generated", "fixture", 1.0)
        page = Page(
            0,
            612,
            792,
            0,
            (
                Element("image", "image", 0, BBox(60, 80, 200, 200), provenance),
                Element("vector", "vector", 0, BBox(50, 60, 500, 500), provenance),
                Element(
                    "continued",
                    "text",
                    0,
                    BBox(50, 600, 200, 12),
                    provenance,
                    text="Figure 3 continued on next page",
                ),
            ),
        )
        document = PhysicalDocument(
            hashlib.sha256(b"cross-page").hexdigest(),
            "fixture",
            "1",
            (page,),
        )
        decisions = plan_region_renders(document, analyze_figures(document))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].status, "rejected")
        self.assertIn("cross_page", decisions[0].reason)

    def test_opt_in_pipeline_retains_native_and_validates_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture.pdf"
            create_region_render_fixture(source)
            output = root / "output"
            config = PaperWrightConfig(
                region_render=RegionRenderPolicy(enabled=True, page_indices=(0,))
            )
            app = PaperWright(config=config, registry=self._registry())
            result = app.convert(source, output)
            validate_manifest(result.manifest)
            rendered = [
                item
                for item in result.manifest["figures"]
                if item["extraction_mode"] == "region-rendered"
            ]
            self.assertEqual(len(rendered), 1)
            figure = rendered[0]
            self.assertTrue(figure["native_asset"]["retained_for_provenance"])
            self.assertTrue(figure["vector_evidence"]["rendered_into_asset"])
            self.assertEqual(
                figure["region_render"]["source_pdf_sha256"],
                result.manifest["source_sha256"],
            )
            markdown = (output / "article.md").read_text(encoding="utf-8")
            image_marker = f"]({figure['asset']['path']})"
            self.assertLess(markdown.index(image_marker), markdown.index("Figure 1."))
            self.assertEqual(
                hashlib.sha256((output / figure["asset"]["path"]).read_bytes()).hexdigest(),
                figure["asset"]["sha256"],
            )

    def test_default_pipeline_does_not_enable_spike_implicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture.pdf"
            create_region_render_fixture(source)
            output = root / "output"
            app = PaperWright(registry=self._registry())
            result = app.convert(source, output)
            self.assertNotIn(
                "region-rendered",
                {item["extraction_mode"] for item in result.manifest["figures"]},
            )


if __name__ == "__main__":
    unittest.main()
