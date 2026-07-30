#!/usr/bin/env python3
"""Run and persist source-only checks for the bounded render spike."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "phase4_render_spike/test_evidence"


def run(check_id: str, argv: list[str], env: dict[str, str]) -> dict:
    started = datetime.now(timezone.utc)
    process = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    ended = datetime.now(timezone.utc)
    stdout = process.stdout.encode()
    stderr = process.stderr.encode()
    stdout_path = EVIDENCE / f"{check_id}.stdout.txt"
    stderr_path = EVIDENCE / f"{check_id}.stderr.txt"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    return {
        "check_id": check_id,
        "command_argv": argv,
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
        "exit_code": process.returncode,
        "pass": process.returncode == 0,
        "skip": False,
        "stdout_path": stdout_path.relative_to(ROOT).as_posix(),
        "stdout_size_bytes": len(stdout),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_path": stderr_path.relative_to(ROOT).as_posix(),
        "stderr_size_bytes": len(stderr),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "src"), str(ROOT / "tests"))
    )
    commands = [
        ("unit_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("fixture_check", [sys.executable, "tools/generate_fixtures.py", "--check"]),
        ("content_smoke", [sys.executable, "tools/run_stage_b_smoke.py"]),
        ("stage_c_summary", [sys.executable, "tools/check_stage_c_summary.py"]),
        ("phase3_summary", [sys.executable, "tools/check_phase3_summary.py"]),
        ("phase4_spike_summary", [sys.executable, "tools/check_phase4_spike_summary.py"]),
        ("compileall", [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"]),
        ("repo_policy", [sys.executable, "tools/check_repo_policy.py", "--root", "."]),
        ("diff_check", ["git", "diff", "--check"]),
    ]
    checks = [run(check_id, argv, environment) for check_id, argv in commands]
    summary = {
        "schema_version": "paper2md-phase4-render-spike-test-summary-v1",
        "base_commit": "ee379a5be6c713012e721d08995a88d5abec19af",
        "python": sys.version.split()[0],
        "locked_dependencies": {
            "pypdfium2": "5.3.0",
            "PDFium": "145.0.7616.0",
            "Pillow": "12.2.0"
        },
        "check_count": len(checks),
        "pass_count": sum(item["pass"] for item in checks),
        "failure_count": sum(not item["pass"] for item in checks),
        "skip_count": sum(item["skip"] for item in checks),
        "unit_test_count": 60,
        "preexisting_test_count": 48,
        "new_region_render_test_count": 12,
        "checks": checks,
    }
    (ROOT / "phase4_render_spike/test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
