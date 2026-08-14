from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from paperwright.batch import classify_error, validate_batch_summary
from paperwright.cli import build_parser, main
from paperwright.config import load_config, with_cli_overrides
from paperwright.exceptions import (
    BackendUnavailableError,
    ConfigurationError,
    CorruptInputError,
    OutputConflictError,
    PathSafetyError,
    UnsupportedInputError,
)
from pdf_fixture_factory import (
    create_auto_region_fixture,
    create_born_digital_fixture,
)


def _tree(root: Path, *, exclude_summary: bool = True) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not (
            exclude_summary
            and path.relative_to(root).as_posix() == "batch_summary.json"
        )
    }


class Phase5AlphaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def _fixture(self, name: str) -> Path:
        path = self.inputs / name
        create_born_digital_fixture(path)
        return path

    def _batch(self, *extra: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = main(list(extra))
        payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
        return code, payload, stderr.getvalue()

    def test_pyproject_exposes_console_script_and_supported_python(self):
        project = tomllib.loads(
            (Path(__file__).parents[1] / "pyproject.toml").read_text(
                encoding="utf-8"
            )
        )["project"]
        self.assertEqual(project["scripts"]["paperwright"], "paperwright.cli:main")
        self.assertIn(">=3.10", project["requires-python"])
        self.assertIn("<3.14", project["requires-python"])

    def test_help_contains_all_alpha_commands(self):
        help_text = build_parser().format_help()
        for command in (
            "convert",
            "batch",
            "validate-model",
            "benchmark-extract",
        ):
            self.assertIn(command, help_text)

    def test_strict_config_and_cli_priority(self):
        config_path = self.root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "backend": "pdfbox",
                    "region_render": {
                        "mode": "auto",
                        "max_candidates_per_document": 2,
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = load_config(config_path)
        self.assertEqual(loaded.backend, "pdfbox")
        self.assertEqual(loaded.region_render.effective_mode, "auto")
        overridden = with_cli_overrides(
            loaded,
            backend="pdfium",
            region_mode="off",
            region_max_candidates=7,
        )
        self.assertEqual(overridden.backend, "pdfium")
        self.assertEqual(overridden.region_render.effective_mode, "off")
        self.assertEqual(
            overridden.region_render.max_candidates_per_document, 7
        )

    def test_unknown_config_field_is_rejected(self):
        path = self.root / "bad.json"
        path.write_text('{"mystery": true}', encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            load_config(path)

    def test_batch_success_is_sorted_and_schema_valid(self):
        self._fixture("zeta.pdf")
        self._fixture("Alpha.pdf")
        output = self.root / "batch"
        code, payload, stderr = self._batch(
            "batch",
            str(output),
            "--input-dir",
            str(self.inputs),
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(payload["counts"]["succeeded"], 2)
        summary = json.loads(
            (output / "batch_summary.json").read_text(encoding="utf-8")
        )
        validate_batch_summary(summary)
        self.assertEqual(
            [item["input_name"] for item in summary["documents"]],
            ["Alpha.pdf", "zeta.pdf"],
        )
        self.assertEqual(
            [item["output_dir"] for item in summary["documents"]],
            ["0001-Alpha", "0002-zeta"],
        )
        self.assertNotIn(str(self.root), json.dumps(summary))

    def test_batch_continue_on_error_preserves_good_document(self):
        (self.inputs / "a-corrupt.pdf").write_bytes(b"%PDF broken")
        self._fixture("b-good.pdf")
        output = self.root / "batch"
        code, payload, _ = self._batch(
            "batch",
            str(output),
            "--input-dir",
            str(self.inputs),
            "--continue-on-error",
        )
        self.assertEqual(code, 3)
        self.assertEqual(payload["counts"], {
            "failed": 1,
            "not_run": 0,
            "succeeded": 1,
            "total": 2,
        })
        summary = json.loads(
            (output / "batch_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["documents"][0]["error"]["category"], "corrupt")
        self.assertFalse((output / "0001-a-corrupt").exists())
        self.assertTrue((output / "0002-b-good/article.md").is_file())

    def test_batch_stop_marks_remaining_not_run(self):
        (self.inputs / "a-corrupt.pdf").write_bytes(b"%PDF broken")
        self._fixture("b-good.pdf")
        output = self.root / "batch"
        code, payload, _ = self._batch(
            "batch",
            str(output),
            "--input-dir",
            str(self.inputs),
        )
        self.assertEqual(code, 3)
        self.assertEqual(payload["counts"]["not_run"], 1)
        summary = json.loads(
            (output / "batch_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["status"], "stopped_on_error")
        self.assertEqual(
            summary["documents"][1]["reason"],
            "stopped_after_previous_error",
        )
        self.assertFalse((output / "0002-b-good").exists())

    def test_batch_two_runs_have_identical_content_hashes(self):
        self._fixture("a.pdf")
        self._fixture("b.pdf")
        outputs = []
        summaries = []
        for name in ("run1", "run2"):
            output = self.root / name
            code, _, _ = self._batch(
                "batch",
                str(output),
                "--input-dir",
                str(self.inputs),
            )
            self.assertEqual(code, 0)
            outputs.append(_tree(output))
            summaries.append(
                json.loads(
                    (output / "batch_summary.json").read_text(encoding="utf-8")
                )
            )
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(
            summaries[0]["deterministic_content_sha256"],
            summaries[1]["deterministic_content_sha256"],
        )
        self.assertTrue(
            summaries[0]["runtime"]["excluded_from_deterministic_content_sha256"]
        )

    def test_existing_batch_output_is_rejected_without_overwrite(self):
        self._fixture("a.pdf")
        output = self.root / "batch"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        code, _, stderr = self._batch(
            "batch",
            str(output),
            "--input-dir",
            str(self.inputs),
        )
        self.assertEqual(code, 2)
        self.assertIn("output_conflict", stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_output_inside_scanned_input_directory_is_rejected(self):
        self._fixture("a.pdf")
        code, _, stderr = self._batch(
            "batch",
            str(self.inputs / "nested-output"),
            "--input-dir",
            str(self.inputs),
        )
        self.assertEqual(code, 2)
        self.assertIn("configuration", stderr)
        self.assertFalse((self.inputs / "nested-output").exists())

    def test_symlinked_input_directory_is_rejected_without_following(self):
        with mock.patch("paperwright.batch.Path.is_symlink", return_value=True):
            code, _, stderr = self._batch(
                "batch",
                str(self.root / "out"),
                "--input-dir",
                str(self.inputs),
            )
        self.assertEqual(code, 2)
        self.assertIn("path_safety", stderr)

    def test_file_list_relative_paths_and_non_recursive_scan(self):
        self._fixture("a.pdf")
        nested = self.inputs / "nested"
        nested.mkdir()
        create_born_digital_fixture(nested / "ignored.pdf")
        listing = self.inputs / "files.txt"
        listing.write_text("# explicit\n./a.pdf\n", encoding="utf-8")
        output = self.root / "out"
        code, payload, _ = self._batch(
            "batch",
            str(output),
            "--file-list",
            str(listing),
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["total"], 1)

    def test_batch_default_off_and_auto_opt_in(self):
        source = self.inputs / "mixed.pdf"
        create_auto_region_fixture(source, "mixed")
        off = self.root / "off"
        auto = self.root / "auto"
        off_code, _, _ = self._batch(
            "batch",
            str(off),
            "--input-file",
            str(source),
        )
        auto_code, _, _ = self._batch(
            "batch",
            str(auto),
            "--input-file",
            str(source),
            "--region-render-mode",
            "auto",
            "--region-render-max-candidates",
            "2",
        )
        self.assertEqual((off_code, auto_code), (0, 0))
        off_manifest = json.loads(
            (off / "0001-mixed/manifest.json").read_text(encoding="utf-8")
        )
        auto_manifest = json.loads(
            (auto / "0001-mixed/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(off_manifest["manifest_version"], "paperwright-manifest-v0.4")
        self.assertEqual(auto_manifest["manifest_version"], "paperwright-manifest-v0.5")
        self.assertEqual(
            sum(
                item["extraction_mode"] == "region-rendered"
                for item in auto_manifest["figures"]
            ),
            1,
        )

    def test_pdfbox_batch_fails_explicitly_without_false_output(self):
        source = self._fixture("a.pdf")
        output = self.root / "out"
        code, _, _ = self._batch(
            "batch",
            str(output),
            "--input-file",
            str(source),
            "--backend",
            "pdfbox",
            "--continue-on-error",
        )
        self.assertEqual(code, 3)
        summary = json.loads(
            (output / "batch_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            summary["documents"][0]["error"]["category"],
            "backend_unavailable",
        )
        self.assertFalse((output / "0001-a").exists())

    def test_error_classification_covers_alpha_categories(self):
        cases = {
            CorruptInputError(): "corrupt",
            UnsupportedInputError(): "unsupported",
            BackendUnavailableError(): "backend_unavailable",
            OutputConflictError(): "output_conflict",
            PathSafetyError(): "path_safety",
            ConfigurationError(): "configuration",
            RuntimeError(): "internal",
        }
        self.assertEqual(
            {classify_error(error) for error in cases},
            set(cases.values()),
        )

    def test_summary_rejects_tampered_deterministic_hash(self):
        source = self._fixture("a.pdf")
        output = self.root / "out"
        code, _, _ = self._batch(
            "batch",
            str(output),
            "--input-file",
            str(source),
        )
        self.assertEqual(code, 0)
        summary = json.loads(
            (output / "batch_summary.json").read_text(encoding="utf-8")
        )
        summary["documents"][0]["input_name"] = "tampered.pdf"
        with self.assertRaisesRegex(ValueError, "deterministic hash"):
            validate_batch_summary(summary)

    def test_corrupt_batch_leaves_no_partial_document_directory(self):
        source = self.inputs / "bad.pdf"
        source.write_bytes(b"%PDF-1.7\ntruncated")
        output = self.root / "out"
        code, _, _ = self._batch(
            "batch",
            str(output),
            "--input-file",
            str(source),
        )
        self.assertEqual(code, 3)
        self.assertEqual(
            [path.name for path in output.iterdir()],
            ["batch_summary.json"],
        )


if __name__ == "__main__":
    unittest.main()
