#!/usr/bin/env python3
"""Run and persist the complete Stage A verification suite."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "stage_a"
LOGS = RESULTS / "logs"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(check_id: str, argv: list[str], expected_exit: int = 0) -> dict:
    started = datetime.now(timezone.utc)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
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
        "expected_exit_code": expected_exit,
        "pass": process.returncode == expected_exit,
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


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    checks = [
        run(
            "fixture_check",
            [sys.executable, "tools/generate_fixtures.py", "--check"],
        ),
        run(
            "unit_tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        run("cli_version", [sys.executable, "-m", "paper2md", "--version"]),
        run(
            "cli_validate_model",
            [
                sys.executable,
                "-m",
                "paper2md",
                "validate-model",
                "tests/fixtures/physical_document.minimal.json",
            ],
        ),
        run(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"],
        ),
        run("git_diff_check", ["git", "diff", "--check"]),
        run(
            "repo_policy",
            [sys.executable, "tools/check_repo_policy.py", "--root", "."],
        ),
    ]
    summary = {
        "schema_version": "paper2md-v2-bootstrap-test-summary-v1",
        "base_commit": "7e559221b46d2204554c303312b2503531b351c0",
        "environment": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "historical_runs": [
            {
                "command": "PYTHONPATH=src python -m unittest discover -s tests -v",
                "result": "25 passed, 2 failed",
                "failure_count": 2,
                "failures": [
                    "test_api_uses_injected_backend: test expectation incorrectly treated registry alias as engine identity",
                    "test_input_inside_output_is_rejected: expected message did not match the correct rejection category",
                ],
                "remediation": "tests/path error classification corrected; complete suite rerun",
                "exact_utc": "not_recorded_before_runner_was_added",
            }
        ],
        "checks": checks,
        "check_count": len(checks),
        "pass_count": sum(item["pass"] for item in checks),
        "failure_count": sum(not item["pass"] for item in checks),
        "skip_count": 0,
    }
    target = RESULTS / "test_summary.json"
    target.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "check_count": summary["check_count"],
                "pass_count": summary["pass_count"],
                "failure_count": summary["failure_count"],
                "skip_count": summary["skip_count"],
            },
            sort_keys=True,
        )
    )
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
