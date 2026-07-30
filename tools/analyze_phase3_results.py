#!/usr/bin/env python3
"""Independently summarize Phase 3 outputs from manifests and files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image, ImageStat

from paper2md.manifest import sha256_file, validate_manifest


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFC", value).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def _bbox_union(records: list[dict]) -> dict[str, float]:
    left = min(item["bbox"]["x"] for item in records)
    top = min(item["bbox"]["y"] for item in records)
    right = max(item["bbox"]["x"] + item["bbox"]["width"] for item in records)
    bottom = max(item["bbox"]["y"] + item["bbox"]["height"] for item in records)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _bbox_equal(first: dict, second: dict) -> bool:
    return all(abs(first[key] - second[key]) <= 1e-6 for key in first)


def _paper(paper: dict, output: Path, baseline: Path | None) -> dict:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    validate_manifest(manifest)
    physical = json.loads(
        (output / "physical_document.json").read_text(encoding="utf-8")
    )
    elements = {
        item["element_id"]: item
        for page in physical["pages"]
        for item in page["elements"]
    }
    markdown = (output / "article.md").read_text(encoding="utf-8")
    title = markdown.splitlines()[0].removeprefix("# ").strip()
    errors: list[str] = []
    blank_assets = 0
    for figure in manifest["figures"]:
        members = [elements[item] for item in figure["member_element_ids"]]
        if not all(item["page_index"] + 1 == figure["page"] for item in members):
            errors.append(f"{figure['figure_id']}:cross_page_member")
        if not _bbox_equal(_bbox_union(members), figure["bbox"]):
            errors.append(f"{figure['figure_id']}:bbox_union_mismatch")
        asset = output / figure["asset"]["path"]
        if sha256_file(asset) != figure["asset"]["sha256"]:
            errors.append(f"{figure['figure_id']}:asset_hash_mismatch")
        with Image.open(asset) as image:
            if image.size != (
                figure["asset"]["width_px"],
                figure["asset"]["height_px"],
            ):
                errors.append(f"{figure['figure_id']}:asset_dimensions_mismatch")
            extrema = ImageStat.Stat(image.convert("RGB")).extrema
            if all(low == high for low, high in extrema):
                blank_assets += 1
                errors.append(f"{figure['figure_id']}:visually_constant_asset")
        caption = figure["caption"]
        if caption["status"] == "matched":
            caption_elements = [elements[item] for item in caption["element_ids"]]
            if not all(item["page_index"] + 1 == figure["page"] for item in caption_elements):
                errors.append(f"{figure['figure_id']}:cross_page_caption")
            if hashlib.sha256(caption["text"].encode("utf-8")).hexdigest() != caption["text_sha256"]:
                errors.append(f"{figure['figure_id']}:caption_text_hash_mismatch")
            figure_marker = f"<!-- figure: {figure['figure_id']};"
            caption_markers = [
                f"<!-- elements: {element_id}" for element_id in caption["element_ids"]
            ]
            figure_position = markdown.find(figure_marker)
            caption_positions = [
                markdown.find(marker) for marker in caption_markers if markdown.find(marker) >= 0
            ]
            if figure_position < 0 or not caption_positions or figure_position > min(caption_positions):
                errors.append(f"{figure['figure_id']}:markdown_not_before_caption")
    output_mismatches = []
    for record in manifest["outputs"]:
        path = output / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != record["size_bytes"]
            or sha256_file(path) != record["sha256"]
        ):
            output_mismatches.append(record["path"])
    baseline_physical_equal = None
    baseline_native_images = None
    if baseline and (baseline / "manifest.json").is_file():
        baseline_manifest = json.loads(
            (baseline / "manifest.json").read_text(encoding="utf-8")
        )
        baseline_native_images = len(baseline_manifest.get("images", []))
        baseline_physical_equal = sha256_file(
            baseline / "physical_document.json"
        ) == sha256_file(output / "physical_document.json")
    figures = manifest["figures"]
    return {
        "paper_id": paper["id"],
        "expected_title": paper["title"],
        "actual_title": title,
        "title_exact_after_format_normalization": _norm(title) == _norm(paper["title"]),
        "page_count": manifest["page_count"],
        "page_marker_count": markdown.count("<!-- page:"),
        "physical_document_unchanged_from_stage_c": baseline_physical_equal,
        "baseline_native_image_count": baseline_native_images,
        "native_image_count": len(manifest["images"]),
        "figure_group_count": len(figures),
        "grouped_group_count": sum(
            item["extraction_mode"] == "grouped" for item in figures
        ),
        "complete_group_count": sum(
            item["evidence_status"] == "complete_native_bitmap_group"
            for item in figures
        ),
        "fragmented_or_degraded_group_count": sum(
            item["evidence_status"] != "complete_native_bitmap_group"
            for item in figures
        ),
        "caption_matched": sum(
            item["caption"]["status"] == "matched" for item in figures
        ),
        "caption_ambiguous": sum(
            item["caption"]["status"] == "ambiguous" for item in figures
        ),
        "caption_none": sum(item["caption"]["status"] == "none" for item in figures),
        "filtered_candidate_count": len(manifest["figure_rejections"]),
        "table_degraded_page_count": sum(
            item.get("code") == "table_structure_degraded"
            for item in manifest["degraded"]
        ),
        "blank_or_constant_figure_asset_count": blank_assets,
        "output_hash_mismatch_count": len(output_mismatches),
        "traceability_error_count": len(errors),
        "traceability_errors": errors,
        "output_file_count": sum(item.is_file() for item in output.rglob("*")),
        "output_size_bytes": sum(
            item.stat().st_size for item in output.rglob("*") if item.is_file()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--run-summary", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    run_summary = json.loads(args.run_summary.read_text(encoding="utf-8"))
    papers = [
        _paper(
            item,
            args.output_root / "run1" / item["id"],
            args.baseline_root / item["id"] if args.baseline_root else None,
        )
        for item in sources["papers"]
    ]
    summary = {
        "summary_version": "paper2md-phase3-figure-caption-summary-v1",
        "base_commit": "8ecd01871eff02e700f0cef1c64cae186be8c69f",
        "source_manifest_sha256": sha256_file(args.sources),
        "frozen_rules_sha256": sha256_file(Path("phase3/frozen_rules_v1.json")),
        "frozen_annotations_sha256": sha256_file(
            Path("phase3/fixtures/figure_caption_cases.json")
        ),
        "papers": papers,
        "totals": {
            "paper_count": len(papers),
            "page_count": sum(item["page_count"] for item in papers),
            "title_exact": sum(
                item["title_exact_after_format_normalization"] for item in papers
            ),
            "page_markers_complete": sum(
                item["page_count"] == item["page_marker_count"] for item in papers
            ),
            "physical_document_unchanged_from_stage_c": sum(
                item["physical_document_unchanged_from_stage_c"] is True
                for item in papers
            ),
            "native_image_count": sum(item["native_image_count"] for item in papers),
            "figure_group_count": sum(item["figure_group_count"] for item in papers),
            "grouped_group_count": sum(item["grouped_group_count"] for item in papers),
            "complete_group_count": sum(item["complete_group_count"] for item in papers),
            "fragmented_or_degraded_group_count": sum(
                item["fragmented_or_degraded_group_count"] for item in papers
            ),
            "caption_matched": sum(item["caption_matched"] for item in papers),
            "caption_ambiguous": sum(item["caption_ambiguous"] for item in papers),
            "caption_none": sum(item["caption_none"] for item in papers),
            "table_degraded_page_count": sum(
                item["table_degraded_page_count"] for item in papers
            ),
            "blank_or_constant_figure_asset_count": sum(
                item["blank_or_constant_figure_asset_count"] for item in papers
            ),
            "traceability_error_count": sum(
                item["traceability_error_count"] for item in papers
            ),
            "output_hash_mismatch_count": sum(
                item["output_hash_mismatch_count"] for item in papers
            ),
            "output_file_count": sum(item["output_file_count"] for item in papers),
            "output_size_bytes": sum(item["output_size_bytes"] for item in papers),
            "determinism_pass": run_summary["determinism_pass_count"],
            "determinism_denominator": run_summary["determinism_denominator"],
            "failure_count": run_summary["totals_run1"]["failure_count"],
            "timeout_count": run_summary["totals_run1"]["timeout_count"],
            "skip_count": run_summary["skip_count"],
        },
        "automatic_check_scope": (
            "hashes, schema-level Python contract, element/page/bbox provenance, "
            "asset dimensions/variance, Markdown ordering, title normalization"
        ),
        "visual_check_required": True,
        "real_publisher_generalization": "not_verified_beyond_frozen_eight_paper_pilot",
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fields = [
        "paper_id",
        "page_count",
        "native_image_count",
        "figure_group_count",
        "grouped_group_count",
        "complete_group_count",
        "fragmented_or_degraded_group_count",
        "caption_matched",
        "caption_ambiguous",
        "caption_none",
        "filtered_candidate_count",
        "table_degraded_page_count",
        "traceability_error_count",
        "output_file_count",
        "output_size_bytes",
    ]
    with args.csv_output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(papers)
    print(json.dumps(summary["totals"], sort_keys=True))
    return 1 if (
        summary["totals"]["traceability_error_count"]
        or summary["totals"]["output_hash_mismatch_count"]
        or summary["totals"]["failure_count"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
