#!/usr/bin/env python3
"""Validate persisted Phase 4 spike claims without PDF/image payloads."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = json.loads(
        (ROOT / "phase4_render_spike/spike_summary.json").read_text(encoding="utf-8")
    )
    annotations = json.loads(
        (ROOT / "phase4_render_spike/frozen_annotations_v1.json").read_text(
            encoding="utf-8"
        )
    )
    visual = json.loads(
        (ROOT / "phase4_render_spike/visual_review.json").read_text(encoding="utf-8")
    )
    checks = {
        "base_commit": summary["base_commit"]
        == "ee379a5be6c713012e721d08995a88d5abec19af",
        "frozen_before_render": annotations["frozen_before_candidate_render"] is True,
        "target_count": len(annotations["targets"]) == 3,
        "regression_paper_count": len(summary["regression"]) == 8,
        "hard_check_count": len(summary["hard_checks"]) == 18,
        "hard_checks_pass": all(summary["hard_checks"].values()),
        "automatic_status": summary["automatic_status"] == "PASS",
        "visual_gate": summary["visual_gate"] == "PASS",
        "visual_review_count": len(visual["reviews"]) == 3,
        "rw2_005_p3_required_pass": next(
            item
            for item in visual["reviews"]
            if item["target_id"] == "P4-RS-RW2-005-P03-FIG1"
        )["decision"]
        == "PASS_COMPLETE_REGION_RENDER",
        "rw2_005_p7_safe_rejection": next(
            item
            for item in visual["reviews"]
            if item["target_id"] == "P4-RS-RW2-005-P07-FIG3"
        )["decision"]
        == "PASS_SAFE_REJECTION",
        "rw2_007_p5_applicable_pass": next(
            item
            for item in visual["reviews"]
            if item["target_id"] == "P4-RS-RW2-007-P05-FIG2"
        )["decision"]
        == "PASS_COMPLETE_REGION_RENDER",
        "target_two_run_determinism": all(
            item["two_runs_identical"] for item in summary["targets"]
        ),
        "phase3_regression_exact": all(
            item["physical_document_identical_to_phase3"]
            and item["article_identical_to_phase3"]
            and item["images_identical_to_phase3"]
            for item in summary["regression"]
        ),
        "no_failure_or_skip": all(
            item["manifest_valid"] for item in summary["regression"]
        ),
    }
    result = {
        "schema_version": "paper2md-phase4-render-spike-persisted-check-v1",
        "check_count": len(checks),
        "pass_count": sum(checks.values()),
        "failure_count": sum(not item for item in checks.values()),
        "skip_count": 0,
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if result["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
