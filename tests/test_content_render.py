from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from paperwright.content_render import (
    analyze_content_regions,
    to_region_render_request,
)
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.region_render import RegionRenderResult
from paperwright.writer import write_outputs


def _element(element_id, kind, bbox, text=None, page_index=0):
    return Element(
        element_id=element_id,
        kind=kind,
        page_index=page_index,
        bbox=bbox,
        text=text,
        provenance=Provenance(
            backend="fixture",
            method="self-generated",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
    )


class ContentRenderTests(unittest.TestCase):
    def test_detects_table_and_equation_candidates(self):
        # page 0: simple table with caption
        table_rows = []
        for row, y in enumerate((400.0, 420.0, 440.0)):
            table_rows.append(_element(f"t{row}a", "text", BBox(100, y, 40, 12), f"col1-{row}"))
            table_rows.append(_element(f"t{row}b", "text", BBox(112, y, 40, 12), f"col2-{row}"))
        table_caption = _element("table-caption", "text", BBox(100, 500, 120, 14), "Table 1 | Fixture")
        page0 = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(*table_rows, table_caption),
        )

        # page 1: display equation with vertical isolation
        page1 = Page(
            page_index=1,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("before", "text", BBox(100, 100, 200, 12), "preceding body line", page_index=1),
                _element("eq", "text", BBox(200, 220, 200, 14), "E = mc2", page_index=1),
                _element("after", "text", BBox(100, 340, 200, 12), "following body line", page_index=1),
            ),
        )
        document = PhysicalDocument(
            source_sha256="a" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page0, page1),
            metadata={"title": "Fixture"},
        )
        analysis = analyze_content_regions(document, max_candidates=12)
        self.assertEqual(len(analysis.tables), 1)
        self.assertEqual(len(analysis.equations), 1)
        table = analysis.tables[0]
        equation = analysis.equations[0]
        self.assertEqual(table.kind, "table")
        self.assertEqual(equation.kind, "equation")
        self.assertTrue(table.caption is not None)
        self.assertEqual(
            to_region_render_request(table, page0).fallback_reason,
            "native_table_rendered_as_image",
        )
        self.assertEqual(
            to_region_render_request(equation, page1).fallback_reason,
            "native_equation_rendered_as_image",
        )

    def test_writer_renders_table_and_equation_assets(self):
        page0 = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("t0a", "text", BBox(100, 400, 40, 12), "col1-0"),
                _element("t0b", "text", BBox(112, 400, 40, 12), "col2-0"),
                _element("t1a", "text", BBox(100, 420, 40, 12), "col1-1"),
                _element("t1b", "text", BBox(112, 420, 40, 12), "col2-1"),
                _element("t2a", "text", BBox(100, 440, 40, 12), "col1-2"),
                _element("t2b", "text", BBox(112, 440, 40, 12), "col2-2"),
                _element("table-caption", "text", BBox(100, 500, 120, 14), "Table 1 | Fixture"),
            ),
        )
        page1 = Page(
            page_index=1,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("before", "text", BBox(100, 100, 200, 12), "preceding body line", page_index=1),
                _element("eq", "text", BBox(200, 220, 200, 14), "E = mc2", page_index=1),
                _element("after", "text", BBox(100, 340, 200, 12), "following body line", page_index=1),
            ),
        )
        document = PhysicalDocument(
            source_sha256="c" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page0, page1),
            metadata={"title": "Fixture"},
        )

        class FakeRenderer:
            def render_region(self, source, request, *, expected_source_sha256):
                data = b"fake-png"
                return RegionRenderResult(
                    figure_id=request.figure_id,
                    data=data,
                    width_px=100,
                    height_px=40,
                    sha256=hashlib.sha256(data).hexdigest(),
                    pixel_variance=10.0,
                    page_area_ratio=0.01,
                    page_rotation=0,
                    renderer_version="fixture",
                    source_sha256=expected_source_sha256,
                    bbox=request.bbox,
                    scale=request.scale,
                    dpi=request.dpi,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            source.write_bytes(b"fake")
            result = write_outputs(
                root=root,
                document=document,
                assets=(),
                backend_warnings=(),
                source=source,
                region_renderer=FakeRenderer(),
                region_render_mode="auto",
                region_render_max_candidates=12,
            )
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest.get("tables", [])), 1)
            self.assertEqual(len(manifest.get("equations", [])), 1)
            article = (root / "article.md").read_text(encoding="utf-8")
            self.assertIn("![Table from page 1]", article)
            self.assertIn("![Equation from page 2]", article)
            self.assertEqual(result.manifest["manifest_version"], "paperwright-manifest-v0.5")

    def test_no_candidates_for_plain_prose_page(self):
        page = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("a", "text", BBox(100, 100, 400, 12), "ordinary prose sentence one"),
                _element("b", "text", BBox(100, 130, 400, 12), "ordinary prose sentence two"),
            ),
        )
        document = PhysicalDocument(
            source_sha256="b" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
            metadata={"title": "Fixture"},
        )
        analysis = analyze_content_regions(document, max_candidates=12)
        self.assertEqual(analysis.tables, ())
        self.assertEqual(analysis.equations, ())


if __name__ == "__main__":
    unittest.main()
