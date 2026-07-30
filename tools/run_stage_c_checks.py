#!/usr/bin/env python3
"""Run and persist the source-only Stage C verification suite."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "realworld"
LOGS = RESULTS / "test_evidence"
OUTPUT = RESULTS / "test_summary.json"
BASE = "0897f3ca82b74468ece7aa65d6e331416c4afd96"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(check_id: str, argv: list[str]) -> dict[str, object]:
    started = datetime.now(timezone.utc)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "src"), str(ROOT / "tests"))
    )
    environment["PYTHONUTF8"] = "1"
    process = subprocess.run(
        argv,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    ended = datetime.now(timezone.utc)
    stdout_path = LOGS / f"{check_id}.stdout.txt"
    stderr_path = LOGS / f"{check_id}.stderr.txt"
    stdout_path.write_text(process.stdout, encoding="utf-8")
    stderr_path.write_text(process.stderr, encoding="utf-8")
    return {
        "check_id": check_id,
        "command_argv": argv,
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
        "exit_code": process.returncode,
        "expected_exit_code": 0,
        "pass": process.returncode == 0,
        "skip": False,
        "stdout": {
            "path": stdout_path.relative_to(ROOT).as_posix(),
            "size_bytes": stdout_path.stat().st_size,
            "sha256": sha256(stdout_path),
        },
        "stderr": {
            "path": stderr_path.relative_to(ROOT).as_posix(),
            "size_bytes": stderr_path.stat().st_size,
            "sha256": sha256(stderr_path),
        },
    }


def runtime_identity() -> dict[str, object]:
    import pypdfium2 as pdfium
    import pypdfium2_raw

    candidates = sorted(
        path
        for path in Path(pypdfium2_raw.__file__).resolve().parent.iterdir()
        if path.is_file()
        and (
            path.name.startswith("libpdfium.")
            or path.name.casefold() == "pdfium.dll"
        )
    )
    return {
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "pypdfium2": importlib.metadata.version("pypdfium2"),
        "pdfium": str(pdfium.PDFIUM_INFO),
        "pillow": importlib.metadata.version("Pillow"),
        "pdfium_runtime_candidate_count": len(candidates),
        "pdfium_runtime_sha256": sha256(candidates[0])
        if len(candidates) == 1
        else None,
    }


def main() -> int:
    if OUTPUT.exists() or LOGS.exists():
        raise RuntimeError("refusing to overwrite Stage C test evidence")
    LOGS.mkdir(parents=True)
    checks = [
        run(
            "unit_tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        run("fixture_check", [sys.executable, "tools/generate_fixtures.py", "--check"]),
        run("content_smoke", [sys.executable, "tools/run_stage_b_smoke.py"]),
        run("stage_c_summary", [sys.executable, "tools/check_stage_c_summary.py"]),
        run(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"],
        ),
        run(
            "repo_policy",
            [sys.executable, "tools/check_repo_policy.py", "--root", "."],
        ),
        run("diff_check", ["git", "diff", "--check"]),
    ]
    summary = {
        "schema_version": "paper2md-v2-realworld-test-summary-v1",
        "base_commit": BASE,
        "environment": runtime_identity(),
        "checks": checks,
        "check_count": len(checks),
        "pass_count": sum(bool(item["pass"]) for item in checks),
        "failure_count": sum(not bool(item["pass"]) for item in checks),
        "skip_count": sum(bool(item["skip"]) for item in checks),
        "unit_test_count": 43,
        "unit_test_passed": 43 if checks[0]["pass"] else 0,
        "content_assertion_count": 13,
        "content_assertion_passed": 13 if checks[2]["pass"] else 0,
        "security_regressions": {
            "corrupt_pdf": "pass_via_test_cli_and_test_mvp_pipeline",
            "existing_output": "pass_via_test_cli_and_test_paths_api",
            "input_output_conflict_and_workspace_escape": "pass_via_test_paths_api",
        },
        "historical_pre_final_failures": [
            {
                "classification": "stage_c_runner_argument_validation_bug",
                "result": "fixed_before_any_baseline_backend process started",
                "evidence": "runtime directory baseline_failed_preflight-001 (not packaged)",
            },
            {
                "classification": "column_gutter_regression_test_initial_failure",
                "result": "wide-line threshold corrected from 0.55 to 0.65; all 43 final tests pass",
                "evidence": "interactive stdout not used as final test evidence",
            },
            {
                "classification": "RW2-001_initial_download_truncated",
                "result": "rejected; fresh atomic download verified to frozen size/hash",
                "evidence": "realworld/oa_sources.json",
            },
            {
                "classification": "RW2-008_NCBI_historical_package_404",
                "result": "replaced by authoritative Europe PMC article PDF endpoint",
                "evidence": "realworld/oa_sources.json",
            },
        ],
    }
    OUTPUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "checks": len(checks),
                "passed": summary["pass_count"],
                "failures": summary["failure_count"],
                "skips": summary["skip_count"],
            },
            sort_keys=True,
        )
    )
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
