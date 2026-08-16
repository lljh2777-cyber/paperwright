import json
import tempfile
import unittest
from pathlib import Path

from paperwright.api import PaperWright
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.completeness import (
    COMPLETENESS_CONTRACT_VERSION,
    build_completeness_report,
    validate_completeness_report,
)
from paperwright.config import PaperWrightConfig
from paperwright.exceptions import ContractValidationError
from paperwright.auto_layout import build_l0_final_layout
from paperwright.layout_candidates import generate_layout_tasks
from paperwright.layout_models import FinalLayout, LayoutTask
from paperwright.layout_writer import write_layout_outputs
from paperwright.manifest import sha256_file, validate_manifest

from pdf_fixture_factory import create_completeness_fixture


class CompletenessGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "completeness.pdf"
        create_completeness_fixture(self.source)
        self.backend = PDFiumBackend()

    def tearDown(self):
        self.temp.cleanup()

    def test_native_text_without_projection_is_invalid(self):
        document = self.backend.extract(
            self.source,
            PaperWrightConfig(),
        ).document
        report = build_completeness_report(
            document,
            projected_text_counts={},
            projected_visual_counts={},
        )
        validate_completeness_report(report)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["pages"][0]["state"], "invalid")
        self.assertEqual(report["pages"][1]["state"], "human_required")
        self.assertEqual(report["pages"][2]["state"], "accepted")
        self.assertEqual(report["pages"][2]["reasons"], ["source_page_blank"])

    def test_direct_writer_renders_non_text_page_and_accepts_blank_page(self):
        product = PaperWright(PaperWrightConfig(workspace_root=self.root))
        product.register_backend("pdfium", self.backend)
        result = product.convert(self.source, self.root / "output")

        report_path = result.output_dir / "_paperwright/completeness-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_completeness_report(report)
        self.assertEqual(
            report["contract_version"],
            COMPLETENESS_CONTRACT_VERSION,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["full_page_fallback"], 1)
        self.assertEqual(
            report["pages"][1]["reasons"],
            ["full_page_fallback_rendered"],
        )
        self.assertEqual(
            report["pages"][2]["reasons"],
            ["source_page_blank"],
        )
        fallback_path = result.output_dir / "images/page-002-fallback.png"
        self.assertTrue(fallback_path.is_file())
        self.assertIn(
            "![Full page fallback from page 2](images/page-002-fallback.png)",
            (result.output_dir / "article.md").read_text(encoding="utf-8"),
        )
        validate_manifest(result.manifest)
        completeness = result.manifest["completeness"]
        self.assertEqual(completeness["report_sha256"], sha256_file(report_path))
        self.assertTrue(
            any(
                item["path"] == "_paperwright/completeness-report.json"
                and item["role"] == "completeness_report"
                for item in result.manifest["outputs"]
            )
        )

    def test_manifest_rejects_completeness_hash_mismatch(self):
        product = PaperWright(PaperWrightConfig(workspace_root=self.root))
        product.register_backend("pdfium", self.backend)
        manifest = product.convert(
            self.source,
            self.root / "tamper-output",
        ).manifest
        manifest["completeness"]["report_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "outputs"):
            validate_manifest(manifest)

    def test_hybrid_writer_uses_the_same_full_page_fallback(self):
        extraction = self.backend.extract(
            self.source,
            PaperWrightConfig(),
        )
        document = extraction.document
        tasks = tuple(
            LayoutTask.from_dict(
                {
                    **task.to_dict(),
                    "metadata": {
                        **task.metadata,
                        "review_mode": "visual-direct",
                        "analysis_roi": {
                            **task.metadata["analysis_roi"],
                            "source": "confirmed:test",
                        },
                    },
                }
            )
            for task in generate_layout_tasks(document)
        )
        layouts = tuple(
            FinalLayout.from_dict(build_l0_final_layout(task, page))
            for task, page in zip(tasks, document.pages, strict=True)
        )
        output = self.root / "hybrid-output"
        result = write_layout_outputs(
            root=output,
            source=self.source,
            document=document,
            assets=extraction.assets,
            backend_warnings=extraction.warnings,
            tasks=tasks,
            layouts=layouts,
            region_renderer=self.backend,
            evidence_level="minimal",
        )

        validate_manifest(result.manifest)
        self.assertEqual(result.manifest["completeness"]["status"], "pass")
        self.assertEqual(
            result.manifest["completeness"]["full_page_fallback"],
            1,
        )
        self.assertTrue(
            (output / "images/page-0002-fallback.png").is_file()
        )
        self.assertIn(
            "Full page fallback from page 2",
            (output / "article.md").read_text(encoding="utf-8"),
        )
        reader = json.loads(
            (output / "_paperwright/reader.json").read_text(encoding="utf-8")
        )
        self.assertTrue(
            any(
                item["path"] == "images/page-0002-fallback.png"
                for item in reader["assets"]
            )
        )


if __name__ == "__main__":
    unittest.main()
