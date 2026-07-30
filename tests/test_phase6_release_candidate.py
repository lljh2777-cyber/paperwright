"""Release-candidate consistency checks that do not alter product algorithms."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from paper2md import __version__
from paper2md.cli import build_parser
from paper2md.config import Paper2MDConfig

ROOT = Path(__file__).resolve().parents[1]


class Phase6ReleaseCandidateTests(unittest.TestCase):
    def test_package_and_runtime_versions_match(self) -> None:
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertEqual(project["version"], __version__)
        self.assertEqual(__version__, "0.6.0a0")
        self.assertEqual(project["requires-python"], ">=3.10,<3.14")

    def test_console_surface_and_safe_defaults(self) -> None:
        parser = build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if getattr(action, "choices", None)
        )
        self.assertEqual(
            set(subparsers.choices),
            {"convert", "batch", "validate-model"},
        )
        config = Paper2MDConfig()
        self.assertEqual(config.backend, "pdfium")
        self.assertEqual(config.region_render.effective_mode, "off")
        self.assertTrue(config.output.atomic_write)
        self.assertFalse(config.output.allow_existing_directory)

    def test_required_schemas_are_packaged(self) -> None:
        package_data = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["setuptools"]["package-data"]["paper2md"]
        self.assertIn("schemas/*.json", package_data)
        for name in (
            "physical_document.schema.json",
            "manifest.schema.json",
            "batch_summary.schema.json",
        ):
            value = json.loads(
                (ROOT / "src/paper2md/schemas" / name).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                value["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )

    def test_rc_docs_match_commands_and_scope(self) -> None:
        combined = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "README.md",
                "docs/QUICKSTART_ALPHA.md",
                "docs/CONFIGURATION.md",
                "docs/TROUBLESHOOTING.md",
                "docs/ALPHA_RC_RELEASE_NOTES.md",
                "docs/SUPPORT_MATRIX.md",
                "REPRODUCE.md",
            )
        )
        for command in (
            "paper2md --version",
            "paper2md --help",
            "paper2md convert",
            "paper2md batch",
            "paper2md validate-model",
        ):
            self.assertIn(command, combined)
        self.assertIn("region-render 默认", combined)
        self.assertIn("PDFBox", combined)
        self.assertIn("不调用生成式 AI", combined)

    def test_phase5_windows_evidence_is_preserved(self) -> None:
        value = json.loads(
            (ROOT / "phase5_alpha/windows_validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(value["unit_tests"]["passed"], 94)
        self.assertEqual(value["batch_checks"]["passed"], 8)
        self.assertEqual(value["install_checks"]["passed"], 12)
        self.assertEqual(value["runtime"]["platform"], "Windows")

    def test_license_boundary_is_not_overclaimed(self) -> None:
        notes = (
            ROOT / "docs/ALPHA_RC_RELEASE_NOTES.md"
        ).read_text(encoding="utf-8")
        matrix = (ROOT / "docs/SUPPORT_MATRIX.md").read_text(encoding="utf-8")
        self.assertIn("项目级许可证仍为 `NOASSERTION`", notes)
        self.assertIn("不批准向公众再分发", notes)
        self.assertIn("wheel/PDFium 二进制分发 | 未批准", matrix)


if __name__ == "__main__":
    unittest.main()
