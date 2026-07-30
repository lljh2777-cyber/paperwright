#!/usr/bin/env python3
"""Validate the checked-in, payload-free Phase 4 auto-region evidence."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = json.loads(
        (ROOT / "phase4_auto_region/auto_region_summary.json").read_text(
            encoding="utf-8"
        )
    )
    inventory = json.loads(
        (ROOT / "phase4_auto_region/auto_candidate_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    visual = json.loads(
        (ROOT / "phase4_auto_region/visual_review_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    tests = json.loads(
        (ROOT / "phase4_auto_region/test_summary.json").read_text(
            encoding="utf-8"
        )
    )
    checks = {
        "base_commit": summary["base_commit"]
        == "25e4ecea02979cf7dcb56ab2d280425bc56e74e2",
        "eight_default_exact": len(summary["default"]) == 8
        and all(
            item["byte_identical_to_authoritative_commit_output"]
            for item in summary["default"]
        ),
        "default_off_compatible": all(
            item["manifest_version"] == "paper2md-manifest-v0.4"
            and item["region_rendered_count"] == 0
            for item in summary["default"]
        ),
        "eight_auto_138_pages": summary["totals"]["papers"] == 8
        and summary["totals"]["pages"] == 138,
        "rendered_inventory": summary["totals"]["rendered_count"] == 2
        and inventory["rendered_count"] == 2,
        "rejected_inventory": inventory["rejected_count"] == 48,
        "hard_checks": len(summary["hard_checks"]) == 12
        and all(summary["hard_checks"].values()),
        "zero_failure_timeout_skip": all(
            summary["totals"][name] == 0
            for name in ("failure_count", "timeout_count", "skip_count")
        ),
        "repeat_determinism": summary["totals"]["repeat_papers"] == 4
        and summary["totals"]["repeat_identical"] == 4,
        "all_rendered_visually_reviewed": visual[
            "all_final_auto_rendered_visually_reviewed"
        ]
        and visual["review_scope"]["auto_rendered_candidates_reviewed"]
        == inventory["rendered_count"],
        "unit_tests": tests["unit_tests"]["total"] == 77
        and tests["unit_tests"]["passed"] == 77
        and tests["unit_tests"]["failed"] == 0
        and tests["unit_tests"]["skipped"] == 0,
    }
    result = {
        "schema_version": "paper2md-phase4-auto-summary-check-v1",
        "checks": checks,
        "total": len(checks),
        "passed": sum(checks.values()),
        "failed": sum(not value for value in checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
