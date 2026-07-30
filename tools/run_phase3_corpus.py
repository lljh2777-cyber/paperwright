#!/usr/bin/env python3
"""Run the frozen eight-paper Phase 3 validation without redistributing PDFs."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import pypdfium2 as pdfium

from paper2md.api import Paper2MD
from paper2md.backends.pdfium import PDFiumBackend
from paper2md.config import Paper2MDConfig
from paper2md.manifest import sha256_file, validate_manifest

DOUBLE_RUN_IDS = {"RW2-003", "RW2-005", "RW2-007", "RW2-008"}


def _tree(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _markdown_refs(path: Path) -> list[str]:
    import re

    return re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", path.read_text(encoding="utf-8"))


def _one_stats(paper_id: str, output: Path, elapsed: float) -> dict:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    validate_manifest(manifest)
    missing_or_mismatched = []
    for record in manifest["outputs"]:
        path = output / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != record["size_bytes"]
            or sha256_file(path) != record["sha256"]
        ):
            missing_or_mismatched.append(record["path"])
    markdown_refs = _markdown_refs(output / "article.md")
    missing_refs = [item for item in markdown_refs if not (output / item).is_file()]
    figures = manifest.get("figures", [])
    return {
        "paper_id": paper_id,
        "status": manifest["status"],
        "page_count": manifest["page_count"],
        "native_image_count": len(manifest.get("images", [])),
        "figure_group_count": len(figures),
        "embedded_group_count": sum(item["extraction_mode"] == "embedded" for item in figures),
        "grouped_group_count": sum(item["extraction_mode"] == "grouped" for item in figures),
        "caption_matched": sum(item["caption"]["status"] == "matched" for item in figures),
        "caption_ambiguous": sum(item["caption"]["status"] == "ambiguous" for item in figures),
        "caption_none": sum(item["caption"]["status"] == "none" for item in figures),
        "markdown_figure_reference_count": len(markdown_refs),
        "markdown_missing_reference_count": len(missing_refs),
        "filtered_candidate_count": len(manifest.get("figure_rejections", [])),
        "degraded_table_page_count": sum(
            item.get("code") == "table_structure_degraded"
            for item in manifest.get("degraded", [])
        ),
        "output_file_count": len(_tree(output)),
        "output_size_bytes": sum(
            item.stat().st_size for item in output.rglob("*") if item.is_file()
        ),
        "manifest_hash_mismatch_count": len(missing_or_mismatched),
        "wall_seconds": elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--sources", type=Path, default=Path("realworld/oa_sources.json")
    )
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    if args.output_root.exists():
        raise SystemExit(f"output root already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)
    product = Paper2MD(Paper2MDConfig(workspace_root=args.output_root.parent))
    product.register_backend("pdfium", PDFiumBackend())
    input_records = []
    results = []
    for paper in sources["papers"]:
        source = args.pdf_root / f"{paper['id']}.pdf"
        digest = sha256_file(source)
        with pdfium.PdfDocument(source) as document:
            page_count = len(document)
        valid = (
            digest == paper["sha256"]
            and source.stat().st_size == paper["size_bytes"]
            and page_count == paper["page_count"]
        )
        input_records.append(
            {
                "paper_id": paper["id"],
                "sha256": digest,
                "size_bytes": source.stat().st_size,
                "page_count": page_count,
                "matches_frozen_manifest": valid,
            }
        )
        if not valid:
            raise SystemExit(f"frozen input mismatch: {paper['id']}")
        started = time.monotonic()
        product.convert(source, args.output_root / "run1" / paper["id"])
        results.append(
            {
                "run": 1,
                **_one_stats(
                    paper["id"],
                    args.output_root / "run1" / paper["id"],
                    time.monotonic() - started,
                ),
            }
        )
        if paper["id"] in DOUBLE_RUN_IDS:
            started = time.monotonic()
            product.convert(source, args.output_root / "run2" / paper["id"])
            results.append(
                {
                    "run": 2,
                    **_one_stats(
                        paper["id"],
                        args.output_root / "run2" / paper["id"],
                        time.monotonic() - started,
                    ),
                }
            )
    determinism = []
    for paper_id in sorted(DOUBLE_RUN_IDS):
        first = _tree(args.output_root / "run1" / paper_id)
        second = _tree(args.output_root / "run2" / paper_id)
        determinism.append(
            {
                "paper_id": paper_id,
                "file_sets_equal": first.keys() == second.keys(),
                "all_file_hashes_equal": first == second,
                "file_count": len(first),
                "different_paths": sorted(
                    key for key in first.keys() | second.keys()
                    if first.get(key) != second.get(key)
                ),
            }
        )
    run1 = [item for item in results if item["run"] == 1]
    summary = {
        "summary_version": "paper2md-phase3-realworld-run-v1",
        "input_manifest_sha256": sha256_file(args.sources),
        "input_records": input_records,
        "runs": results,
        "totals_run1": {
            "paper_count": len(run1),
            "page_count": sum(item["page_count"] for item in run1),
            "native_image_count": sum(item["native_image_count"] for item in run1),
            "figure_group_count": sum(item["figure_group_count"] for item in run1),
            "grouped_group_count": sum(item["grouped_group_count"] for item in run1),
            "caption_matched": sum(item["caption_matched"] for item in run1),
            "caption_ambiguous": sum(item["caption_ambiguous"] for item in run1),
            "caption_none": sum(item["caption_none"] for item in run1),
            "filtered_candidate_count": sum(item["filtered_candidate_count"] for item in run1),
            "degraded_table_page_count": sum(item["degraded_table_page_count"] for item in run1),
            "output_file_count": sum(item["output_file_count"] for item in run1),
            "output_size_bytes": sum(item["output_size_bytes"] for item in run1),
            "failure_count": sum(item["status"] == "failed" for item in run1),
            "timeout_count": 0,
            "manifest_hash_mismatch_count": sum(
                item["manifest_hash_mismatch_count"] for item in run1
            ),
            "markdown_missing_reference_count": sum(
                item["markdown_missing_reference_count"] for item in run1
            ),
        },
        "determinism": determinism,
        "determinism_pass_count": sum(
            item["all_file_hashes_equal"] for item in determinism
        ),
        "determinism_denominator": len(determinism),
        "skip_count": 0,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary["totals_run1"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
