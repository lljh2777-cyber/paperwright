import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from paper2md.cli import main
from pdf_fixture_factory import create_born_digital_fixture


class CLITests(unittest.TestCase):
    def test_validate_fixture(self):
        fixture = Path(__file__).parent / "fixtures/physical_document.minimal.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["validate-model", str(fixture)])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["page_count"], 1)
        self.assertEqual(len(payload["deterministic_sha256"]), 64)

    def test_invalid_model_has_nonzero_exit_and_message(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["validate-model", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("输入或契约错误", stderr.getvalue())

    def test_convert_rejects_corrupt_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.pdf"
            source.write_bytes(b"%PDF-1.4\\n% deliberately corrupt\\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["convert", str(source), str(root / "out")])
            self.assertEqual(code, 2)
            self.assertIn("PDFium", stderr.getvalue())

    def test_convert_rejects_existing_output_before_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.pdf"
            source.write_bytes(b"%PDF-1.4\\n")
            output = root / "out"
            output.mkdir()
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["convert", str(source), str(output)])
            self.assertEqual(code, 2)
            self.assertIn("拒绝覆盖", stderr.getvalue())

    def test_convert_real_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "convert",
                        str(source),
                        str(root / "out"),
                        "--workspace-root",
                        str(root),
                    ]
                )
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["page_count"], 2)
            self.assertTrue((root / "out/article.md").is_file())


if __name__ == "__main__":
    unittest.main()
