#!/usr/bin/env python3
"""Validate payload-free Phase 5 Alpha evidence and preserved baselines."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical_text_bytes(path: Path) -> bytes:
    """Return repository text bytes with Git's CRLF checkout normalized."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def matches_preserved_text(
    path: Path, expected_size: int, expected_sha: str
) -> bool:
    content = canonical_text_bytes(path)
    return (
        len(content) == expected_size
        and hashlib.sha256(content).hexdigest() == expected_sha
    )


def main() -> int:
    baseline = json.loads(
        (ROOT / "phase5_alpha/baseline_authority.json").read_text(
            encoding="utf-8"
        )
    )
    tests = json.loads(
        (ROOT / "phase5_alpha/test_summary.json").read_text(encoding="utf-8")
    )
    batch = json.loads(
        (ROOT / "phase5_alpha/batch_test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    install = json.loads(
        (ROOT / "phase5_alpha/install_test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    licenses = json.loads(
        (ROOT / "phase5_alpha/license_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    protected = all(
        matches_preserved_text(
            ROOT / item["path"], item["size_bytes"], item["sha256"]
        )
        for item in baseline["preserved_evidence"]
    )
    machine_text = "\n".join(
        (
            json.dumps(batch, ensure_ascii=False),
            json.dumps(install, ensure_ascii=False),
            json.dumps(tests, ensure_ascii=False),
        )
    )
    leaked_absolute_path = bool(
        re.search(r"(?:/workspace/|/opt/|[A-Za-z]:\\\\)", machine_text)
    )
    checks = {
        "authorized_base": baseline["git"]["commit"]
        == "5656eeff3d95ed7a3f025c5763bd94c5be565abe",
        "preserved_evidence_hashes": protected,
        "phase4_algorithm_unchanged": hashlib.sha256(
            canonical_text_bytes(ROOT / "src/paper2md/region_render.py")
        ).hexdigest()
        == "111ee10acedbc8113f56b0e933152164b349e6579aa47caa43ec5bb2ff86f7d1",
        "unit_tests": tests["unit_tests"]["passed"] == 94
        and tests["unit_tests"]["failed"] == 0
        and tests["unit_tests"]["skipped"] == 0,
        "batch_checks": batch["case_count"] == 8
        and batch["pass_count"] == 8
        and batch["failure_count"] == 0
        and batch["skip_count"] == 0
        and all(batch["content_assertions"].values()),
        "install_checks": install["install_count"] == 2
        and install["command_check_count"] == 12
        and install["pass_count"] == 12
        and install["failure_count"] == 0
        and install["skip_count"] == 0
        and install["wheel_sdist_outputs_deterministic"],
        "license_inventory": licenses["counts"]["components"] == 9
        and not licenses["conclusion"]["known_actual_license_conflict"]
        and licenses["conclusion"]["formal_binary_distribution"]
        == "not_approved",
        "machine_evidence_has_no_absolute_workspace_path": (
            not leaked_absolute_path
        ),
    }
    result = {
        "schema_version": "paper2md-phase5-alpha-summary-check-v1",
        "checks": checks,
        "total": len(checks),
        "passed": sum(checks.values()),
        "failed": sum(not value for value in checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
