import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from paper2md.backends.base import ExtractedAsset
from paper2md.figures import analyze_figures
from paper2md.manifest import validate_manifest
from paper2md.models import BBox, Element, Page, PhysicalDocument, Provenance
from paper2md.writer import write_outputs


ROOT = Path(__file__).parents[1]
GOLD = ROOT / "phase3/fixtures/figure_caption_cases.json"


def _document(case):
    objects_by_page = {}
    for sequence, item in enumerate(case["objects"]):
        metadata = {"line_group": sequence, "raw_object_index": sequence}
        element = Element(
            element_id=item["id"],
            kind=item["kind"],
            page_index=item["page"],
            bbox=BBox.from_dict(item["bbox"]),
            text=item.get("text"),
            source_object_id=f"fixture:{item['id']}",
            provenance=Provenance(
                backend="fixture",
                method="frozen_project_authored_annotation",
                source_ref=f"{case['case_id']}:{item['id']}",
                confidence=1.0,
            ),
            metadata=metadata,
        )
        objects_by_page.setdefault(item["page"], []).append(element)
    pages = tuple(
        Page(
            item["page_index"],
            item["width"],
            item["height"],
            0,
            tuple(objects_by_page.get(item["page_index"], [])),
        )
        for item in case["pages"]
    )
    return PhysicalDocument(
        source_sha256=hashlib.sha256(case["case_id"].encode()).hexdigest(),
        backend="fixture",
        backend_version="1",
        pages=pages,
        metadata={"pdf_metadata": {}},
    )


def _asset(element_id, color):
    image = Image.new("RGB", (24, 18), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return ExtractedAsset(
        element_id,
        f"{element_id}.png",
        "image/png",
        buffer.getvalue(),
        24,
        18,
    )


class Phase3FigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = json.loads(GOLD.read_text(encoding="utf-8"))

    def test_all_frozen_group_and_caption_expectations(self):
        for case in self.gold["cases"]:
            with self.subTest(case=case["case_id"]):
                analysis = analyze_figures(_document(case))
                expected = case["expected"]
                self.assertEqual(len(analysis.groups), expected["group_count"])
                self.assertEqual(
                    [set(item.member_element_ids) for item in analysis.groups],
                    [set(item) for item in expected["groups"]],
                )
                self.assertEqual(
                    [item.caption_status for item in analysis.groups],
                    expected["caption_statuses"],
                )

    def test_cross_page_caption_is_never_paired(self):
        case = next(
            item for item in self.gold["cases"]
            if item["case_id"] == "FC-007-cross-page-rejection"
        )
        group = analyze_figures(_document(case)).groups[0]
        self.assertEqual(group.page_index, 0)
        self.assertIsNone(group.caption)
        self.assertEqual(group.caption_status, "none")

    def test_grouped_asset_and_caption_are_adjacent_in_markdown(self):
        case = next(
            item for item in self.gold["cases"]
            if item["case_id"] == "FC-008-caption-local-order"
        )
        document = _document(case)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "out"
            root.mkdir()
            result = write_outputs(
                root=root,
                document=document,
                assets=(_asset("img-1", (200, 20, 30)),),
                backend_warnings=(),
            )
            markdown = result.article_path.read_text(encoding="utf-8")
            self.assertLess(markdown.index("Body before"), markdown.index("![Figure"))
            self.assertLess(markdown.index("![Figure"), markdown.index("Figure 7."))
            self.assertLess(markdown.index("Figure 7."), markdown.index("Body after"))
            validate_manifest(result.manifest)
            self.assertEqual(result.manifest["figures"][0]["caption"]["status"], "matched")

    def test_multipanel_composite_is_real_and_preserves_native_assets(self):
        case = next(
            item for item in self.gold["cases"]
            if item["case_id"] == "FC-002-multipanel"
        )
        document = _document(case)
        assets = (
            _asset("panel-a", (220, 20, 20)),
            _asset("panel-b", (20, 220, 20)),
            _asset("panel-c", (20, 20, 220)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "out"
            root.mkdir()
            result = write_outputs(
                root=root,
                document=document,
                assets=assets,
                backend_warnings=(),
            )
            figure = result.manifest["figures"][0]
            self.assertEqual(figure["extraction_mode"], "grouped")
            self.assertIn(
                figure["evidence_status"],
                {
                    "complete_native_bitmap_group",
                    "degraded_bitmap_group_with_unrendered_vector_evidence",
                },
            )
            self.assertEqual(len(result.manifest["images"]), 3)
            composite = root / figure["asset"]["path"]
            with Image.open(composite) as image:
                colors = image.convert("RGB").getcolors(maxcolors=1000)
                self.assertGreaterEqual(len(colors or []), 3)
            self.assertEqual(
                hashlib.sha256(composite.read_bytes()).hexdigest(),
                figure["asset"]["sha256"],
            )

    def test_ambiguous_caption_is_rejected_not_forced(self):
        case = next(
            item for item in self.gold["cases"]
            if item["case_id"] == "FC-005-ambiguous-caption"
        )
        group = analyze_figures(_document(case)).groups[0]
        self.assertEqual(group.caption_status, "ambiguous")
        self.assertIsNone(group.caption)
        self.assertIn("multiple_caption", group.caption_reason)


if __name__ == "__main__":
    unittest.main()
