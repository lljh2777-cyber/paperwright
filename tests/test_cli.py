import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from paperwright.cli import main
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

    def test_benchmark_extract_reports_stage_and_page_timings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["benchmark-extract", str(source)])

            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "profiled")
            self.assertEqual(result["page_count"], 2)
            performance = result["performance"]
            self.assertEqual(
                performance["schema_version"],
                "paperwright-extraction-timing-v0.1",
            )
            self.assertEqual(performance["page_count"], 2)
            self.assertGreaterEqual(performance["total_ms"], 0)
            self.assertEqual(len(performance["pages"]), 2)
            for page in performance["pages"]:
                self.assertGreaterEqual(page["character_scan_ms"], 0)
                self.assertGreaterEqual(page["object_walk_ms"], 0)
                self.assertGreaterEqual(page["reading_order_ms"], 0)
                self.assertIn("native_object_counts", page)
                self.assertIn("emitted_element_counts", page)

    def test_benchmark_text_only_skips_native_object_walk(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fixture.pdf"
            create_born_digital_fixture(source)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "benchmark-extract",
                        str(source),
                        "--mode",
                        "text-only",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(
                result["performance"]["extraction_mode"],
                "text-only",
            )
            for page in result["performance"]["pages"]:
                self.assertEqual(page["object_walk_ms"], 0.0)
                self.assertEqual(
                    page["emitted_element_counts"]["image"],
                    0,
                )
                self.assertEqual(
                    page["emitted_element_counts"]["vector"],
                    0,
                )

    def test_benchmark_raster_reports_masks_and_visual_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fixture.pdf"
            create_born_digital_fixture(source)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "benchmark-extract",
                        str(source),
                        "--mode",
                        "raster",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            raster = result["performance"]["raster_layout"]
            self.assertEqual(
                raster["schema_version"],
                "paperwright-raster-benchmark-v0.1",
            )
            self.assertEqual(len(raster["pages"]), 2)
            self.assertEqual(len(raster["layout_tasks"]), 2)
            self.assertGreaterEqual(raster["render_total_ms"], 0)
            self.assertGreaterEqual(raster["layout_task_total_ms"], 0)
            for page in raster["pages"]:
                self.assertEqual(
                    page["contract_version"],
                    "paperwright-raster-layout-v0.1",
                )
                self.assertEqual(len(page["mask_sha256"]["ink"]), 64)
                self.assertGreaterEqual(page["analysis_ms"], 0)
            self.assertTrue(raster["pages"][0]["regions"])
            self.assertTrue(
                all(
                    task["contract_version"]
                    == "paperwright-layout-task-v0.2"
                    for task in raster["layout_tasks"]
                )
            )
            self.assertGreater(
                raster["layout_tasks"][0]["raster_candidate_count"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
