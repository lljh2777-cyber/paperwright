#!/usr/bin/env python3
"""Check Phase 6 persisted evidence and protected Phase 5 inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical_text_bytes(path: Path) -> bytes:
    """Return repository text bytes with Git's CRLF checkout normalized."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def matches_protected_text(
    path: Path, expected_size: int, expected_sha: str
) -> bool:
    content = canonical_text_bytes(path)
    return (
        len(content) == expected_size
        and hashlib.sha256(content).hexdigest() == expected_sha
    )


def main() -> int:
    baseline = json.loads(
        (ROOT / "phase6_alpha_rc/baseline_authority.json").read_text(
            encoding="utf-8"
        )
    )
    tests = json.loads(
        (ROOT / "phase6_alpha_rc/test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    install = json.loads(
        (ROOT / "phase6_alpha_rc/install_test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    batch = json.loads(
        (ROOT / "phase6_alpha_rc/batch_test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    license_decision = json.loads(
        (ROOT / "phase6_alpha_rc/license_decision.json").read_text(
            encoding="utf-8"
        )
    )
    readiness = json.loads(
        (ROOT / "phase6_alpha_rc/release_readiness.json").read_text(
            encoding="utf-8"
        )
    )
    protected = all(
        matches_protected_text(
            ROOT / item["path"], item["size_bytes"], item["sha256"]
        )
        for item in baseline["protected_files"]
    )
    checks = {
        "authorized_baseline": baseline["commit"]
        == "47e31abb58d062e1da0ecf92a2a303afddaa39af",
        "protected_phase5_evidence": protected,
        "unit_tests_100": tests["unit_tests"]
        == {
            "inherited": 94,
            "phase6_added": 6,
            "total": 100,
            "passed": 100,
            "failed": 0,
            "skipped": 0,
        },
        "batch_8": batch["pass_count"] == 8
        and batch["failure_count"] == batch["skip_count"] == 0,
        "install_12": install["pass_count"] == 12
        and install["failure_count"] == install["skip_count"] == 0
        and all(install["content_assertions"].values()),
        "safe_defaults": all(
            readiness["behavior"][key]
            for key in (
                "default_backend_pdfium",
                "region_render_default_off",
                "region_render_auto_opt_in",
                "pdfbox_explicit_unavailable",
                "path_safety",
                "atomic_output",
                "deterministic_single_and_batch",
            )
        ),
        "license_not_overclaimed": (
            not license_decision["actual_license_conflict_found"]
            and license_decision["decisions"][
                "public_source_package_redistribution"
            ]["status"]
            == "not_approved"
            and license_decision["decisions"][
                "pdfium_bundled_binary_distribution"
            ]["status"]
            == "not_approved"
        ),
        "release_scope": readiness["overall"] == "PASS_WITH_LIMITATIONS"
        and readiness["formal_distribution"] == "not_approved",
    }
    result = {
        "schema_version": "paper2md-phase6-summary-check-v1",
        "checks": checks,
        "total": len(checks),
        "passed": sum(checks.values()),
        "failed": sum(not value for value in checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
