import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from paper2md.api import Paper2MD
from paper2md.backends.pdfium import PDFiumBackend
from paper2md.config import Paper2MDConfig
from paper2md.manifest import sha256_file, validate_manifest
from paper2md.models import PhysicalDocument

from pdf_fixture_factory import create_born_digital_fixture


class MVPPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "fixture.pdf"
        self.fixture_info = create_born_digital_fixture(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def product(self):
        product = Paper2MD(Paper2MDConfig(workspace_root=self.root))
        product.register_backend("pdfium", PDFiumBackend())
        return product

    def convert(self, name="out"):
        return self.product().convert(self.source, self.root / name)

    def test_real_pipeline_outputs_and_traceability(self):
        result = self.convert()
        output = result.output_dir
        self.assertEqual(
            set(path.name for path in output.iterdir()),
            {"article.md", "images", "manifest.json", "physical_document.json"},
        )
        manifest = json.loads(
            (output / "manifest.json").read_text(encoding="utf-8")
        )
        validate_manifest(manifest)
        self.assertEqual(manifest["source_sha256"], self.fixture_info["sha256"])
        physical = PhysicalDocument.from_dict(
            json.loads(
                (output / "physical_document.json").read_text(encoding="utf-8")
            )
        )
        self.assertEqual(len(physical.pages), 2)
        self.assertEqual(
            {item["element_id"] for item in manifest["elements"]},
            {
                element.element_id
                for page in physical.pages
                for element in page.elements
            },
        )

    def test_title_and_basic_double_column_order(self):
        self.convert()
        markdown = (self.root / "out/article.md").read_text(encoding="utf-8")
        self.assertTrue(markdown.startswith("# Paper2MD Fixture Title\n"))
        self.assertLess(markdown.index("LEFT-ONE"), markdown.index("LEFT-TWO"))
        self.assertLess(markdown.index("LEFT-TWO"), markdown.index("RIGHT-ONE"))
        self.assertLess(markdown.index("RIGHT-ONE"), markdown.index("RIGHT-TWO"))

    def test_unicode_winansi_text_is_preserved(self):
        self.convert()
        markdown = (self.root / "out/article.md").read_text(encoding="utf-8")
        self.assertIn("Café", markdown)

    def test_text_only_extraction_matches_full_native_text(self):
        backend = PDFiumBackend()
        full = backend.extract(self.source, Paper2MDConfig()).document
        fast_result = backend.extract_text_only(self.source, Paper2MDConfig())
        fast = fast_result.document

        for full_page, fast_page in zip(full.pages, fast.pages, strict=True):
            full_text = [
                (
                    item.text,
                    item.bbox.to_dict(),
                    item.metadata.get("font_name"),
                    item.metadata.get("font_size"),
                )
                for item in full_page.elements
                if item.kind == "text"
            ]
            fast_text = [
                (
                    item.text,
                    item.bbox.to_dict(),
                    item.metadata.get("font_name"),
                    item.metadata.get("font_size"),
                )
                for item in fast_page.elements
                if item.kind == "text"
            ]
            self.assertEqual(fast_text, full_text)
            self.assertTrue(
                all(item.kind == "text" for item in fast_page.elements)
            )

        self.assertEqual(
            fast_result.performance["extraction_mode"],
            "text-only",
        )
        self.assertTrue(
            all(
                page["object_walk_ms"] == 0.0
                for page in fast_result.performance["pages"]
            )
        )

    def test_embedded_image_is_real_and_hash_matches(self):
        result = self.convert()
        manifest = result.manifest
        self.assertEqual(len(manifest["images"]), 1)
        record = manifest["images"][0]
        image_path = result.output_dir / record["path"]
        self.assertEqual(sha256_file(image_path), record["sha256"])
        with Image.open(image_path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (16, 12))
            colors = image.convert("RGB").getcolors(maxcolors=1000)
            self.assertGreater(len(colors or []), 2)

    def test_table_is_degraded_without_fabricated_markdown_grid(self):
        result = self.convert()
        markdown = (
            result.article_path.read_text(encoding="utf-8")
            if hasattr(result, "article_path")
            else (result.output_dir / "article.md").read_text(encoding="utf-8")
        )
        self.assertIn("degraded", markdown)
        self.assertIn("Alpha", markdown)
        self.assertNotIn("| Group | Value |", markdown)
        self.assertEqual(
            [item["code"] for item in result.manifest["degraded"]],
            ["table_structure_degraded"],
        )

    def test_two_runs_are_byte_deterministic(self):
        first = self.convert("run1").output_dir
        second = self.convert("run2").output_dir
        first_files = sorted(
            path.relative_to(first) for path in first.rglob("*") if path.is_file()
        )
        second_files = sorted(
            path.relative_to(second) for path in second.rglob("*") if path.is_file()
        )
        self.assertEqual(first_files, second_files)
        for relative in first_files:
            self.assertEqual(
                hashlib.sha256((first / relative).read_bytes()).hexdigest(),
                hashlib.sha256((second / relative).read_bytes()).hexdigest(),
                relative,
            )

    def test_corrupt_input_leaves_no_partial_output(self):
        corrupt = self.root / "corrupt.pdf"
        corrupt.write_bytes(b"%PDF-1.7\ncorrupt")
        product = self.product()
        with self.assertRaises(Exception):
            product.convert(corrupt, self.root / "failed-out")
        self.assertFalse((self.root / "failed-out").exists())
        self.assertEqual(list(self.root.glob(".failed-out.paper2md-*")), [])

    def test_manifest_output_hashes_match(self):
        result = self.convert()
        for record in result.manifest["outputs"]:
            path = result.output_dir / record["path"]
            self.assertEqual(path.stat().st_size, record["size_bytes"])
            self.assertEqual(sha256_file(path), record["sha256"])


if __name__ == "__main__":
    unittest.main()
