#!/usr/bin/env python3
"""Recompute preregistered aggregate metrics from an immutable E7 run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from paperwright.grobid_evaluation import (
    aggregate_grobid_evidence_summaries,
    canonical_grobid_evaluation_json,
    summarize_grobid_review,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    report_path = root / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summaries = []
    documents = []
    for document in report["documents"]:
        row = {
            "document_id": document["document_id"],
            "status": document["status"],
            "native_status": document["native"]["status"],
            "grobid_status": document["grobid_crf"]["status"],
        }
        if document["status"] == "complete":
            review = root / document["document_id"] / "grobid-crf"
            summary = summarize_grobid_review(review)
            summaries.append(summary)
            row["claim_count"] = summary["claim_count"]
        else:
            row["native_returncode"] = document["native"]["returncode"]
            row["grobid_returncode"] = document["grobid_crf"]["returncode"]
        documents.append(row)
    value = {
        "contract_version": "paperwright-grobid-semantic-machine-summary-v0.1",
        "source_report": {
            "path": "report.json",
            "sha256": _sha256(report_path),
        },
        "document_count": len(documents),
        "complete_pair_count": len(summaries),
        "failed_pair_count": len(documents) - len(summaries),
        "documents": documents,
        "by_claim_type": aggregate_grobid_evidence_summaries(summaries),
        "semantic_accuracy_measured": False,
    }
    payload = canonical_grobid_evaluation_json(value)
    destination = args.output or (root / "machine-summary.json")
    if destination.exists():
        raise SystemExit(f"machine summary 已存在，拒绝覆盖: {destination}")
    destination.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
