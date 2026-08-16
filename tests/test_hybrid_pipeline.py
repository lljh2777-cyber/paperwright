from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from paperwright.api import PaperWright
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.exceptions import ContractValidationError
from paperwright.hybrid import (
    HYBRID_RUN_CONTRACT_VERSION,
    HybridPipeline,
    HybridResolverRequest,
    validate_hybrid_run,
)

from pdf_fixture_factory import create_born_digital_fixture


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(SRC)
        + os.pathsep
        + str(ROOT / "tests")
        + (os.pathsep + current if current else "")
    )
    return environment


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv],
        cwd=ROOT,
        env=_env(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_resolver_stage(request: HybridResolverRequest) -> None:
    result = _run(
        [
            str(ROOT / "tools" / "run_routing_plan.py"),
            str(request.input_pdf),
            str(request.review_dir),
            str(request.output_dir),
            "--stage",
            request.stage,
            "--resolution-plan",
            str(request.run_dir / "resolve-issues.json"),
            "--reviewed-output",
            str(request.reviewed_output_dir),
            "--evidence",
            request.evidence_level,
            "--references",
            request.references_mode,
            "--extraction-profile",
            request.extraction_profile,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


class HybridPipelineTests(unittest.TestCase):
    def test_missing_roi_pauses_with_bound_run_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            product = PaperWright(PaperWrightConfig(workspace_root=root))
            product.register_backend("pdfium", PDFiumBackend())

            result = HybridPipeline(product).run(
                source,
                root / "output",
                run_dir=root / "run",
                extraction_profile="fast",
                preview_scale=0.5,
            )

            self.assertEqual(result.state["status"], "awaiting_input")
            self.assertEqual(
                result.state["next_action"]["kind"],
                "confirm_content_roi",
            )
            self.assertEqual(
                result.state["contract_version"],
                HYBRID_RUN_CONTRACT_VERSION,
            )
            validate_hybrid_run(result.state)
            persisted = json.loads(
                (root / "run" / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, result.state)
            self.assertFalse((root / "output").exists())

    def test_validator_rejects_duplicate_artifact_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            product = PaperWright(PaperWrightConfig(workspace_root=root))
            product.register_backend("pdfium", PDFiumBackend())
            state = HybridPipeline(product).run(
                source,
                root / "output",
                run_dir=root / "run",
                extraction_profile="fast",
                preview_scale=0.5,
            ).state
            invalid = deepcopy(state)
            invalid["artifacts"].append(deepcopy(invalid["artifacts"][0]))
            with self.assertRaises(ContractValidationError):
                validate_hybrid_run(invalid)

    def test_validator_rejects_non_sequential_stage_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            product = PaperWright(PaperWrightConfig(workspace_root=root))
            product.register_backend("pdfium", PDFiumBackend())
            state = HybridPipeline(product).run(
                source,
                root / "output",
                run_dir=root / "run",
                extraction_profile="fast",
                preview_scale=0.5,
            ).state
            invalid = deepcopy(state)
            invalid["stages"][2]["status"] = "completed"
            with self.assertRaisesRegex(
                ContractValidationError, "current_stage 之后"
            ):
                validate_hybrid_run(invalid)

    def test_cli_resumes_and_completes_l0_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            output = root / "output"
            run_dir = root / "run"
            common = [
                "-m",
                "paperwright",
                "hybrid",
                str(source),
                str(output),
                "--run-dir",
                str(run_dir),
                "--extraction-profile",
                "fast",
                "--preview-scale",
                "0.5",
                "--evidence",
                "minimal",
            ]
            first = _run(common)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["status"], "awaiting_input")

            proposal_path = run_dir / "layout-proposal" / "content-roi.json"
            confirmed_path = root / "confirmed-roi.json"
            confirmed = json.loads(proposal_path.read_text(encoding="utf-8"))
            confirmed["review_status"] = "confirmed"
            confirmed["reviewer"] = "test"
            confirmed_path.write_text(
                json.dumps(confirmed, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            second = _run(
                common
                + [
                    "--resume",
                    "--content-roi-json",
                    str(confirmed_path),
                ]
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(Path(payload["active_output_dir"]), output)
            self.assertTrue((output / "article.md").is_file())

            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            validate_hybrid_run(state)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(
                [item["status"] for item in state["stages"]],
                ["completed"] * 5,
            )
            self.assertEqual(
                [item["name"] for item in state["stages"]],
                ["evidence", "layout", "projection", "text", "verification"],
            )
            self.assertTrue(
                any(
                    item["role"].startswith("final_layout_")
                    for item in state["artifacts"]
                )
            )
            self.assertTrue(
                any(
                    item["role"] == "resolution_plan"
                    for item in state["artifacts"]
                )
            )
            self.assertNotIn("token", json.dumps(state).casefold())
            self.assertNotIn("cost", json.dumps(state).casefold())

            layout_artifact = next(
                item
                for item in state["artifacts"]
                if item["role"].startswith("final_layout_")
            )
            layout_path = Path(layout_artifact["path"])
            layout_path.write_text(
                layout_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            tampered_resume = _run(
                common
                + [
                    "--resume",
                    "--content-roi-json",
                    str(confirmed_path),
                ]
            )
            self.assertNotEqual(tampered_resume.returncode, 0)
            self.assertIn("哈希不匹配", tampered_resume.stderr)

    def test_resume_skips_completed_layout_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            product = PaperWright(PaperWrightConfig(workspace_root=root))
            product.register_backend("pdfium", PDFiumBackend())
            output = root / "output"
            run_dir = root / "run"

            proposal = HybridPipeline(product).run(
                source,
                output,
                run_dir=run_dir,
                extraction_profile="fast",
                preview_scale=0.5,
                evidence_level="minimal",
            )
            roi = json.loads(
                Path(proposal.state["next_action"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            roi["review_status"] = "confirmed"
            roi["reviewer"] = "test"
            confirmed_path = root / "confirmed-roi.json"
            confirmed_path.write_text(
                json.dumps(roi, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            calls: list[str] = []

            def fail_projection(request: HybridResolverRequest) -> None:
                calls.append(request.stage)
                if request.stage == "projection":
                    raise RuntimeError("injected projection failure")
                _run_resolver_stage(request)

            with self.assertRaisesRegex(RuntimeError, "injected projection"):
                HybridPipeline(product, resolver=fail_projection).run(
                    source,
                    output,
                    run_dir=run_dir,
                    content_roi_json=confirmed_path,
                    resume=True,
                    extraction_profile="fast",
                    preview_scale=0.5,
                    evidence_level="minimal",
                )
            self.assertEqual(calls, ["layout", "projection"])
            failed = json.loads(
                (run_dir / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed["current_stage"], "projection")
            self.assertEqual(
                [item["status"] for item in failed["stages"]],
                ["completed", "completed", "failed", "pending", "pending"],
            )

            resumed_calls: list[str] = []

            def resume_resolver(request: HybridResolverRequest) -> None:
                resumed_calls.append(request.stage)
                _run_resolver_stage(request)

            result = HybridPipeline(product, resolver=resume_resolver).run(
                source,
                output,
                run_dir=run_dir,
                content_roi_json=confirmed_path,
                resume=True,
                extraction_profile="fast",
                preview_scale=0.5,
                evidence_level="minimal",
            )
            self.assertEqual(result.state["status"], "completed")
            self.assertEqual(resumed_calls, ["projection", "text"])
            self.assertEqual(
                next(
                    item["attempts"]
                    for item in result.state["stages"]
                    if item["name"] == "layout"
                ),
                1,
            )
            self.assertEqual(
                next(
                    item["attempts"]
                    for item in result.state["stages"]
                    if item["name"] == "projection"
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
