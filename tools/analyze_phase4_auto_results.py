#!/usr/bin/env python3
"""Recompute default and auto-mode machine claims from runtime outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from paper2md.manifest import validate_manifest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--default-runtime", type=Path, required=True)
    parser.add_argument("--auto-runtime", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--inventory-json", type=Path, required=True)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    args = parser.parse_args()

    papers = json.loads(
        (args.repo / "realworld/oa_sources.json").read_text(encoding="utf-8")
    )["papers"]
    default_rows = []
    auto_rows = []
    candidates = []
    all_rendered_assets_valid = True
    all_native_retained = True
    all_adjacent = True
    for paper in papers:
        paper_id = paper["id"]
        default_root = args.default_runtime / "first" / paper_id
        default_equal = tree(default_root) == tree(
            args.baseline_root / paper_id
        )
        default_manifest = json.loads(
            (default_root / "manifest.json").read_text(encoding="utf-8")
        )
        validate_manifest(default_manifest)
        default_rows.append(
            {
                "paper_id": paper_id,
                "byte_identical_to_authoritative_commit_output": default_equal,
                "manifest_version": default_manifest["manifest_version"],
                "region_rendered_count": sum(
                    item["extraction_mode"] == "region-rendered"
                    for item in default_manifest["figures"]
                ),
            }
        )

        output_root = args.auto_runtime / "first" / paper_id
        manifest = json.loads(
            (output_root / "manifest.json").read_text(encoding="utf-8")
        )
        validate_manifest(manifest)
        article = (output_root / "article.md").read_text(encoding="utf-8")
        rendered = []
        rejected = []
        for figure in manifest["figures"]:
            region = figure["region_render"]
            if region["status"] == "rendered":
                asset_path = output_root / figure["asset"]["path"]
                asset_valid = (
                    asset_path.is_file()
                    and sha256(asset_path) == figure["asset"]["sha256"]
                    and asset_path.stat().st_size
                    == figure["asset"]["size_bytes"]
                )
                asset_marker = f"]({figure['asset']['path']})"
                caption_element_ids = set(
                    figure["caption"].get("element_ids", [])
                )
                adjacent = False
                if (
                    figure["caption"]["status"] == "matched"
                    and asset_marker in article
                    and caption_element_ids
                ):
                    # The caption may span multiple element groups and its
                    # displayed whitespace can differ from the manifest text.
                    # Adjacency therefore means that the first provenance
                    # group after the approved asset contains a caption
                    # evidence element, not that the full caption string is a
                    # literal Markdown substring.
                    tail = article[
                        article.index(asset_marker) + len(asset_marker) :
                    ]
                    match = re.match(
                        r"\s*<!-- elements:\s*([^;]+);\s*page:\s*\d+\s*-->",
                        tail,
                    )
                    if match:
                        next_ids = {
                            value.strip()
                            for value in match.group(1).split(",")
                        }
                        adjacent = bool(
                            next_ids.intersection(caption_element_ids)
                        )
                native_retained = (
                    figure["native_asset"]["retained_for_provenance"] is True
                    and (output_root / figure["native_asset"]["path"]).is_file()
                )
                all_rendered_assets_valid &= asset_valid
                all_native_retained &= native_retained
                all_adjacent &= adjacent
                record = {
                    "candidate_id": (
                        f"{paper_id}:{figure['figure_id']}:rendered"
                    ),
                    "paper_id": paper_id,
                    "figure_id": figure["figure_id"],
                    "page": figure["page"],
                    "status": "rendered",
                    "reason": region["reason"],
                    "bbox": figure["bbox"],
                    "page_area_ratio": region["page_area_ratio"],
                    "dpi": region["dpi"],
                    "width_px": region["width_px"],
                    "height_px": region["height_px"],
                    "asset_path_runtime_only": figure["asset"]["path"],
                    "asset_sha256": figure["asset"]["sha256"],
                    "asset_valid": asset_valid,
                    "caption_id": figure["caption"]["caption_id"],
                    "caption_text_sha256": figure["caption"]["text_sha256"],
                    "caption_adjacent": adjacent,
                    "native_asset_retained": native_retained,
                    "vector_evidence_count": figure["vector_evidence"]["count"],
                }
                candidates.append(record)
                rendered.append(record)
            elif region["status"] == "rejected":
                record = {
                    "candidate_id": (
                        f"{paper_id}:{figure['figure_id']}:rejected"
                    ),
                    "paper_id": paper_id,
                    "figure_id": figure["figure_id"],
                    "page": figure["page"],
                    "status": "rejected",
                    "reason": region["reason"],
                    "bbox": figure["bbox"],
                    "page_area_ratio": None,
                    "dpi": None,
                    "width_px": None,
                    "height_px": None,
                    "asset_path_runtime_only": None,
                    "asset_sha256": None,
                    "asset_valid": None,
                    "caption_id": figure["caption"]["caption_id"],
                    "caption_text_sha256": figure["caption"]["text_sha256"],
                    "caption_adjacent": None,
                    "native_asset_retained": (
                        figure["native_asset"]["retained_for_provenance"] is True
                    ),
                    "vector_evidence_count": figure["vector_evidence"]["count"],
                }
                candidates.append(record)
                rejected.append(record)
        global_rejections = [
            item
            for item in manifest["figure_rejections"]
            if item.get("evidence_status") == "region_render_rejected"
            and item.get("figure_id") is None
        ]
        for sequence, item in enumerate(global_rejections, start=1):
            candidates.append(
                {
                    "candidate_id": (
                        f"{paper_id}:page-{item['page']:03d}:"
                        f"global-rejection-{sequence:03d}"
                    ),
                    "paper_id": paper_id,
                    "figure_id": None,
                    "page": item["page"],
                    "status": "rejected",
                    "reason": item["reason"],
                    "bbox": None,
                    "page_area_ratio": None,
                    "dpi": None,
                    "width_px": None,
                    "height_px": None,
                    "asset_path_runtime_only": None,
                    "asset_sha256": None,
                    "asset_valid": None,
                    "caption_id": None,
                    "caption_text_sha256": None,
                    "caption_adjacent": None,
                    "native_asset_retained": None,
                    "vector_evidence_count": len(
                        item.get("evidence_element_ids", [])
                    ),
                }
            )
        auto_rows.append(
            {
                "paper_id": paper_id,
                "page_count": manifest["page_count"],
                "figure_count": len(manifest["figures"]),
                "rendered_count": len(rendered),
                "figure_rejected_count": len(rejected),
                "global_rejected_count": len(global_rejections),
                "degraded_count": len(manifest["degraded"]),
                "output_file_count": sum(
                    path.is_file() for path in output_root.rglob("*")
                ),
                "output_bytes": sum(
                    path.stat().st_size
                    for path in output_root.rglob("*")
                    if path.is_file()
                ),
            }
        )

    runtime_index = json.loads(
        (args.auto_runtime / "runtime_index.json").read_text(encoding="utf-8")
    )
    deterministic = {
        item["paper_id"]: item["identical_to_first"]
        for item in runtime_index["repeat_runs"]
    }
    rw5_p3 = [
        item
        for item in candidates
        if item["paper_id"] == "RW2-005"
        and item["page"] == 3
        and item["status"] == "rendered"
    ]
    rw5_p7 = [
        item
        for item in candidates
        if item["paper_id"] == "RW2-005"
        and item["page"] == 7
        and item["status"] == "rejected"
        and item["reason"]
        == "cross_page_figure_continuation_explicitly_detected"
    ]
    rw7_p5 = [
        item
        for item in candidates
        if item["paper_id"] == "RW2-007"
        and item["page"] == 5
        and item["status"] == "rendered"
    ]
    hard_checks = {
        "eight_default_outputs_byte_identical": all(
            item["byte_identical_to_authoritative_commit_output"]
            for item in default_rows
        ),
        "default_manifest_remains_v04": all(
            item["manifest_version"] == "paper2md-manifest-v0.4"
            for item in default_rows
        ),
        "default_has_zero_region_renders": all(
            item["region_rendered_count"] == 0 for item in default_rows
        ),
        "eight_auto_documents_completed": len(auto_rows) == 8,
        "all_rendered_assets_hash_valid": all_rendered_assets_valid,
        "all_rendered_native_assets_retained": all_native_retained,
        "all_rendered_captions_adjacent": all_adjacent,
        "all_required_repeat_runs_deterministic": bool(deterministic)
        and all(deterministic.values()),
        "rw2_005_page3_general_rule_rendered": len(rw5_p3) == 1,
        "rw2_005_page7_cross_page_rejected": len(rw5_p7) >= 1,
        "rw2_007_page5_general_rule_rendered": len(rw7_p5) == 1,
        "failure_timeout_skip_zero": (
            runtime_index["failure_count"]
            + runtime_index["timeout_count"]
            + runtime_index["skip_count"]
            == 0
        ),
    }
    summary = {
        "schema_version": "paper2md-phase4-auto-region-summary-v1",
        "base_commit": "25e4ecea02979cf7dcb56ab2d280425bc56e74e2",
        "default": default_rows,
        "auto": auto_rows,
        "totals": {
            "papers": len(auto_rows),
            "pages": sum(item["page_count"] for item in auto_rows),
            "rendered_count": sum(item["rendered_count"] for item in auto_rows),
            "figure_rejected_count": sum(
                item["figure_rejected_count"] for item in auto_rows
            ),
            "global_rejected_count": sum(
                item["global_rejected_count"] for item in auto_rows
            ),
            "degraded_count": sum(item["degraded_count"] for item in auto_rows),
            "output_file_count": sum(
                item["output_file_count"] for item in auto_rows
            ),
            "output_bytes": sum(item["output_bytes"] for item in auto_rows),
            "repeat_papers": len(deterministic),
            "repeat_identical": sum(deterministic.values()),
            "failure_count": runtime_index["failure_count"],
            "timeout_count": runtime_index["timeout_count"],
            "skip_count": runtime_index["skip_count"],
        },
        "determinism": deterministic,
        "hard_checks": hard_checks,
        "automatic_conclusion": (
            "PASS" if all(hard_checks.values()) else "FAIL"
        ),
        "visual_review_required_for_rendered_count": sum(
            item["status"] == "rendered" for item in candidates
        ),
    }
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    inventory = {
        "schema_version": "paper2md-phase4-auto-candidate-inventory-v1",
        "candidate_count": len(candidates),
        "rendered_count": sum(
            item["status"] == "rendered" for item in candidates
        ),
        "rejected_count": sum(
            item["status"] == "rejected" for item in candidates
        ),
        "candidates": candidates,
    }
    args.inventory_json.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    columns = [
        "candidate_id",
        "paper_id",
        "figure_id",
        "page",
        "status",
        "reason",
        "page_area_ratio",
        "width_px",
        "height_px",
        "asset_sha256",
        "caption_id",
        "caption_adjacent",
        "native_asset_retained",
        "vector_evidence_count",
    ]
    with args.inventory_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
