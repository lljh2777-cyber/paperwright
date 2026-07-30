#!/usr/bin/env python3
"""Validate the persisted Stage C machine summary without runtime payloads."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = json.loads(
        (ROOT / "realworld" / "realworld_summary.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (ROOT / "realworld" / "oa_sources.json").read_text(encoding="utf-8")
    )
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    checks = {
        "source_count": len(sources["papers"]) == 8,
        "source_pages": sources["totals"]["page_count"] == 138,
        "source_hashes_unique": len(
            {paper["sha256"] for paper in sources["papers"]}
        )
        == 8,
        "summary_count": len(summary["papers"]) == 8,
        "conversion_success": summary["totals"]["success_count"] == 8,
        "title_exact": summary["totals"]["title_exact_normalized"] == 8,
        "no_missing_nonempty_page": summary["totals"][
            "no_missing_nonempty_page"
        ]
        == 8,
        "images_valid": summary["totals"]["image_count"]
        == summary["totals"]["valid_image_count"],
        "tables_honest": summary["totals"][
            "fabricated_markdown_table_grid_lines"
        ]
        == 0,
        "determinism": summary["totals"]["determinism_paper_count"]
        == summary["totals"]["determinism_pass_count"]
        == 4,
        "machine_failures": summary["totals"]["failure_count"] == 0,
        "no_tracked_pdf": not any(path.lower().endswith(".pdf") for path in tracked),
    }
    result = {
        "schema_version": "paper2md-v2-realworld-summary-check-v1",
        "checks": checks,
        "check_count": len(checks),
        "pass_count": sum(checks.values()),
        "failure_count": sum(not value for value in checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
