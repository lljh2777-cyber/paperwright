#!/usr/bin/env python3
"""Check persisted Phase 3 evidence without requiring OA PDF payloads."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = json.loads((ROOT / "phase3/phase3_summary.json").read_text(encoding="utf-8"))
    visual = json.loads((ROOT / "phase3/visual_review.json").read_text(encoding="utf-8"))
    annotations = json.loads(
        (ROOT / "phase3/fixtures/figure_caption_cases.json").read_text(encoding="utf-8")
    )
    totals = summary["totals"]
    checks = {
        "base_commit": summary["base_commit"]
        == "8ecd01871eff02e700f0cef1c64cae186be8c69f",
        "frozen_case_count": len(annotations["cases"]) == 8,
        "paper_count": totals["paper_count"] == 8,
        "page_count": totals["page_count"] == 138,
        "title_exact": totals["title_exact"] == 8,
        "page_markers": totals["page_markers_complete"] == 8,
        "physical_document_unchanged": totals[
            "physical_document_unchanged_from_stage_c"
        ] == 8,
        "has_grouped_figure": totals["grouped_group_count"] >= 1,
        "has_matched_caption": totals["caption_matched"] >= 1,
        "honest_degraded_groups": totals["fragmented_or_degraded_group_count"] >= 1,
        "no_blank_figure_asset": totals["blank_or_constant_figure_asset_count"] == 0,
        "traceability": totals["traceability_error_count"] == 0,
        "output_hashes": totals["output_hash_mismatch_count"] == 0,
        "determinism": totals["determinism_pass"]
        == totals["determinism_denominator"]
        == 4,
        "fail_timeout_skip": (
            totals["failure_count"],
            totals["timeout_count"],
            totals["skip_count"],
        )
        == (0, 0, 0),
        "visual_scope": visual["totals"]["primary_papers_reviewed"] == 8,
        "visual_no_fake_page": visual["totals"][
            "blank_or_whole_page_fake_figures_observed"
        ] == 0,
    }
    payload = {
        "check_version": "paper2md-phase3-persisted-summary-check-v1",
        "check_count": len(checks),
        "pass_count": sum(checks.values()),
        "failure_count": sum(not item for item in checks.values()),
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if payload["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
