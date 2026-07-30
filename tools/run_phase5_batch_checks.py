#!/usr/bin/env python3
"""Run payload-free Alpha batch acceptance cases and persist machine evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from pdf_fixture_factory import create_auto_region_fixture, create_born_digital_fixture


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix() != "batch_summary.json"
    }


def run_case(
    case_id: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expected_exit: int,
) -> dict[str, object]:
    process = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if process.returncode != expected_exit:
        raise RuntimeError(
            f"{case_id}: expected {expected_exit}, got {process.returncode}: "
            f"{process.stdout}\n{process.stderr}"
        )
    portable_argv = []
    for value in argv:
        portable = value.replace(str(cwd), "<repo>")
        portable = portable.replace(str(cwd.parent), "<workspace>")
        if value == sys.executable:
            portable = "<python>"
        portable_argv.append(portable)
    return {
        "case_id": case_id,
        "command_argv": portable_argv,
        "expected_exit": expected_exit,
        "actual_exit": process.returncode,
        "pass": True,
        "stdout_size_bytes": len(process.stdout.encode()),
        "stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
        "stderr_size_bytes": len(process.stderr.encode()),
        "stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    runtime = args.output_root.resolve()
    if runtime.exists():
        raise RuntimeError("batch check runtime exists")
    runtime.mkdir(parents=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo / "src")
    environment["PYTHONUTF8"] = "1"
    command = [sys.executable, "-m", "paper2md"]

    normal = runtime / "normal-inputs"
    normal.mkdir()
    create_born_digital_fixture(normal / "zeta.pdf")
    create_born_digital_fixture(normal / "Alpha.pdf")
    cases = []
    for sequence in (1, 2):
        cases.append(
            run_case(
                f"deterministic_success_run_{sequence}",
                command
                + [
                    "batch",
                    str(runtime / f"success-{sequence}"),
                    "--input-dir",
                    str(normal),
                ],
                cwd=repo,
                env=environment,
                expected_exit=0,
            )
        )
    first_summary = json.loads(
        (runtime / "success-1/batch_summary.json").read_text(encoding="utf-8")
    )
    second_summary = json.loads(
        (runtime / "success-2/batch_summary.json").read_text(encoding="utf-8")
    )
    deterministic = (
        tree(runtime / "success-1") == tree(runtime / "success-2")
        and first_summary["deterministic_content_sha256"]
        == second_summary["deterministic_content_sha256"]
    )
    if not deterministic:
        raise RuntimeError("two batch success runs are not deterministic")

    partial = runtime / "partial-inputs"
    partial.mkdir()
    (partial / "a-corrupt.pdf").write_bytes(b"%PDF broken")
    create_born_digital_fixture(partial / "b-good.pdf")
    cases.append(
        run_case(
            "continue_on_error",
            command
            + [
                "batch",
                str(runtime / "continue"),
                "--input-dir",
                str(partial),
                "--continue-on-error",
            ],
            cwd=repo,
            env=environment,
            expected_exit=3,
        )
    )
    continue_summary = json.loads(
        (runtime / "continue/batch_summary.json").read_text(encoding="utf-8")
    )
    if (
        continue_summary["counts"]
        != {"total": 2, "succeeded": 1, "failed": 1, "not_run": 0}
        or continue_summary["documents"][0]["error"]["category"] != "corrupt"
        or not (runtime / "continue/0002-b-good/article.md").is_file()
    ):
        raise RuntimeError("continue-on-error content assertion failed")

    cases.append(
        run_case(
            "stop_on_error",
            command
            + [
                "batch",
                str(runtime / "stop"),
                "--input-dir",
                str(partial),
            ],
            cwd=repo,
            env=environment,
            expected_exit=3,
        )
    )
    stop_summary = json.loads(
        (runtime / "stop/batch_summary.json").read_text(encoding="utf-8")
    )
    if (
        stop_summary["counts"]["not_run"] != 1
        or (runtime / "stop/0002-b-good").exists()
    ):
        raise RuntimeError("stop-on-error content assertion failed")

    mixed = runtime / "mixed.pdf"
    create_auto_region_fixture(mixed, "mixed")
    cases.append(
        run_case(
            "auto_opt_in",
            command
            + [
                "batch",
                str(runtime / "auto"),
                "--input-file",
                str(mixed),
                "--region-render-mode",
                "auto",
                "--region-render-max-candidates",
                "2",
            ],
            cwd=repo,
            env=environment,
            expected_exit=0,
        )
    )
    auto_manifest = json.loads(
        (runtime / "auto/0001-mixed/manifest.json").read_text(encoding="utf-8")
    )
    if (
        auto_manifest["manifest_version"] != "paper2md-manifest-v0.5"
        or sum(
            item["extraction_mode"] == "region-rendered"
            for item in auto_manifest["figures"]
        )
        != 1
    ):
        raise RuntimeError("auto opt-in content assertion failed")

    cases.append(
        run_case(
            "pdfbox_unavailable",
            command
            + [
                "batch",
                str(runtime / "pdfbox"),
                "--input-file",
                str(normal / "Alpha.pdf"),
                "--backend",
                "pdfbox",
                "--continue-on-error",
            ],
            cwd=repo,
            env=environment,
            expected_exit=3,
        )
    )
    pdfbox_summary = json.loads(
        (runtime / "pdfbox/batch_summary.json").read_text(encoding="utf-8")
    )
    if (
        pdfbox_summary["documents"][0]["error"]["category"]
        != "backend_unavailable"
    ):
        raise RuntimeError("PDFBox unavailable classification failed")

    conflict = runtime / "conflict"
    conflict.mkdir()
    marker = conflict / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    cases.append(
        run_case(
            "output_conflict",
            command
            + [
                "batch",
                str(conflict),
                "--input-dir",
                str(normal),
            ],
            cwd=repo,
            env=environment,
            expected_exit=2,
        )
    )
    if marker.read_text(encoding="utf-8") != "keep":
        raise RuntimeError("output conflict overwrote marker")

    cases.append(
        run_case(
            "nested_output_rejected",
            command
            + [
                "batch",
                str(normal / "nested"),
                "--input-dir",
                str(normal),
            ],
            cwd=repo,
            env=environment,
            expected_exit=2,
        )
    )
    if (normal / "nested").exists():
        raise RuntimeError("nested output was created")

    summary = {
        "schema_version": "paper2md-phase5-batch-test-summary-v1",
        "case_count": len(cases),
        "pass_count": sum(bool(item["pass"]) for item in cases),
        "failure_count": 0,
        "skip_count": 0,
        "cases": cases,
        "content_assertions": {
            "deterministic_sort": [
                item["input_name"] for item in first_summary["documents"]
            ]
            == ["Alpha.pdf", "zeta.pdf"],
            "two_run_output_tree_identical": deterministic,
            "runtime_excluded_from_content_hash": first_summary["runtime"][
                "excluded_from_deterministic_content_sha256"
            ],
            "continue_isolates_failure": True,
            "stop_marks_remaining_not_run": True,
            "auto_opt_in_manifest_v05": True,
            "pdfbox_explicit_failure": True,
            "conflict_preserves_existing_data": True,
            "nested_output_not_created": True,
        },
        "runtime_payload": "external_runtime_only_not_for_source_package",
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
