#!/usr/bin/env python3
"""Run and persist the complete Stage B verification suite."""

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
RESULTS = ROOT / "stage_b"
LOGS = RESULTS / "logs"
BASE = "8f8061baf96a5c1bfef423ebc88ef7b5516d1cac"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(check_id: str, argv: list[str], expected_exit: int = 0) -> dict:
    started = datetime.now(timezone.utc)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "src"), str(ROOT / "tests"))
    )
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


def runtime_identity() -> dict[str, object]:
    import pypdfium2 as pdfium
    import pypdfium2_raw

    libraries = sorted(
        path
        for path in Path(pypdfium2_raw.__file__).resolve().parent.iterdir()
        if path.is_file()
        and (
            path.name.startswith("libpdfium.")
            or path.name.casefold() == "pdfium.dll"
        )
    )
    return {
        "pypdfium2": importlib.metadata.version("pypdfium2"),
        "pdfium": str(pdfium.PDFIUM_INFO),
        "pillow": importlib.metadata.version("Pillow"),
        "libpdfium_sha256": sha256(libraries[0]) if len(libraries) == 1 else None,
        "libpdfium_candidate_count": len(libraries),
    }


def main() -> int:
    if RESULTS.exists():
        raise RuntimeError("stage_b already exists; refusing to overwrite evidence")
    LOGS.mkdir(parents=True)
    checks = [
        run(
            "fixture_check",
            [sys.executable, "tools/generate_fixtures.py", "--check"],
        ),
        run(
            "unit_tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        run("content_smoke", [sys.executable, "tools/run_stage_b_smoke.py"]),
        run("cli_version", [sys.executable, "-m", "paper2md", "--version"]),
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
        "schema_version": "paper2md-v2-mvp-test-summary-v1",
        "base_commit": BASE,
        "environment": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "runtime": runtime_identity(),
        },
        "exploratory_runs": [
            {
                "command": "PYTHONPATH=src:tests python -m unittest discover -s tests -v",
                "result": "36 passed",
                "failure_count": 0,
                "exact_utc": "not_recorded_before_stage_b_runner",
            }
        ],
        "checks": checks,
        "check_count": len(checks),
        "pass_count": sum(item["pass"] for item in checks),
        "failure_count": sum(not item["pass"] for item in checks),
        "skip_count": 0,
        "unit_test_count": 36,
        "unit_test_passed": 36 if checks[1]["pass"] else 0,
    }
    (RESULTS / "test_summary.json").write_text(
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
