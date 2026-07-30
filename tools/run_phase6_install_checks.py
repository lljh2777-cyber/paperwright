#!/usr/bin/env python3
"""Run Phase 6 wheel/sdist installation checks without retaining artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.output_root.exists():
        raise RuntimeError("Phase 6 install runtime already exists")
    with tempfile.TemporaryDirectory(
        prefix="paper2md-phase6-install-summary-"
    ) as temporary:
        underlying = Path(temporary) / "phase5-compatible-summary.json"
        command = [
            sys.executable,
            str(repo / "tools/run_phase5_install_checks.py"),
            "--repo",
            str(repo),
            "--output-root",
            str(args.output_root),
            "--summary",
            str(underlying),
        ]
        environment = dict(os.environ)
        environment["PYTHONUTF8"] = "1"
        process = subprocess.run(
            command,
            cwd=repo,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if process.returncode:
            raise RuntimeError(
                "underlying install checks failed: "
                f"{process.stdout}\n{process.stderr}"
            )
        value = json.loads(underlying.read_text(encoding="utf-8"))
    checks = [
        check
        for install in value["installs"]
        for check in install["checks"]
    ]
    required = {"install", "version", "help", "convert", "batch", "validate_model"}
    per_kind = {
        install["kind"]: {check["check_id"] for check in install["checks"]}
        for install in value["installs"]
    }
    assertions = {
        "two_artifacts_built_and_audited": len(value["artifacts"]) == 2,
        "wheel_commands_complete": per_kind.get("wheel") == required,
        "sdist_commands_complete": per_kind.get("sdist") == required,
        "all_commands_zero_exit": all(check["exit_code"] == 0 for check in checks),
        "content_outputs_match_between_artifacts": value[
            "wheel_sdist_outputs_deterministic"
        ],
        "three_schemas_in_wheel": value["artifacts"][0]["contents"][
            "required_schemas_present"
        ],
        "no_forbidden_build_members": all(
            artifact["contents"]["forbidden_members"] == 0
            for artifact in value["artifacts"]
        ),
        "build_artifacts_runtime_only": value[
            "artifacts_are_runtime_only_not_for_source_package"
        ],
    }
    if not all(assertions.values()):
        raise RuntimeError(f"Phase 6 install content assertion failed: {assertions}")
    result = {
        "schema_version": "paper2md-phase6-alpha-rc-install-summary-v1",
        "baseline_commit": "47e31abb58d062e1da0ecf92a2a303afddaa39af",
        "candidate_version": "0.6.0a0",
        "platform_scope": (
            "Linux Work cloud measured in Phase 6; Windows evidence is inherited "
            "read-only from phase5_alpha/windows_validation.json"
        ),
        "python": value["python"],
        "artifacts": value["artifacts"],
        "installs": value["installs"],
        "install_count": value["install_count"],
        "command_check_count": value["command_check_count"],
        "pass_count": value["pass_count"],
        "failure_count": value["failure_count"],
        "skip_count": value["skip_count"],
        "content_assertions": assertions,
        "wheel_sdist_outputs_deterministic": value[
            "wheel_sdist_outputs_deterministic"
        ],
        "artifacts_are_runtime_only_not_for_source_package": True,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
