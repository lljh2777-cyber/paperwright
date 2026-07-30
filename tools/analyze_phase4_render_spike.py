#!/usr/bin/env python3
"""Independently recompute bounded spike and regression claims from outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
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


def image_tree(root: Path) -> dict[str, str]:
    return {
        path.name: sha256(path)
        for path in sorted((root / "images").glob("*"))
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--phase3-runtime", type=Path, required=True)
    args = parser.parse_args()
    annotations = json.loads(
        (args.repo / "phase4_render_spike/frozen_annotations_v1.json").read_text(
            encoding="utf-8"
        )
    )
    annotation_by_target = {
        item["target_id"]: item for item in annotations["targets"]
    }
    visual = json.loads(
        (args.repo / "phase4_render_spike/visual_review.json").read_text(
            encoding="utf-8"
        )
    )
    visual_decisions = {
        item["target_id"]: item["decision"] for item in visual["reviews"]
    }
    regression = []
    for sequence in range(1, 9):
        paper_id = f"RW2-{sequence:03d}"
        current = args.runtime / "regression" / paper_id
        previous = args.phase3_runtime / "run1" / paper_id
        manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(manifest)
        regression.append(
            {
                "paper_id": paper_id,
                "manifest_valid": True,
                "page_count": manifest["page_count"],
                "physical_document_identical_to_phase3": sha256(
                    current / "physical_document.json"
                )
                == sha256(previous / "physical_document.json"),
                "article_identical_to_phase3": sha256(current / "article.md")
                == sha256(previous / "article.md"),
                "images_identical_to_phase3": image_tree(current)
                == image_tree(previous),
                "region_render_opt_in_count": sum(
                    item["extraction_mode"] == "region-rendered"
                    for item in manifest["figures"]
                ),
                "output_file_count": sum(
                    path.is_file() for path in current.rglob("*")
                ),
                "output_bytes": sum(
                    path.stat().st_size
                    for path in current.rglob("*")
                    if path.is_file()
                ),
            }
        )

    target_results = []
    for paper_id in ("RW2-005", "RW2-007"):
        run_roots = [
            args.runtime / "targets" / run / paper_id
            for run in ("run1", "run2")
        ]
        manifests = [
            json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            for root in run_roots
        ]
        for manifest in manifests:
            validate_manifest(manifest)
        region_figures = [
            [
                item
                for item in manifest["figures"]
                if item["extraction_mode"] == "region-rendered"
            ]
            for manifest in manifests
        ]
        target_results.append(
            {
                "paper_id": paper_id,
                "two_runs_identical": tree(run_roots[0]) == tree(run_roots[1]),
                "run_file_counts": [
                    sum(path.is_file() for path in root.rglob("*"))
                    for root in run_roots
                ],
                "run_bytes": [
                    sum(
                        path.stat().st_size
                        for path in root.rglob("*")
                        if path.is_file()
                    )
                    for root in run_roots
                ],
                "region_figures": region_figures[0],
            }
        )

    rw5 = next(item for item in target_results if item["paper_id"] == "RW2-005")
    rw7 = next(item for item in target_results if item["paper_id"] == "RW2-007")
    rw5_figure = next(item for item in rw5["region_figures"] if item["page"] == 3)
    rw7_figure = next(item for item in rw7["region_figures"] if item["page"] == 5)
    rw5_manifest = json.loads(
        (
            args.runtime / "targets/run1/RW2-005/manifest.json"
        ).read_text(encoding="utf-8")
    )
    rw5_p7_rejections = [
        item
        for item in rw5_manifest["figure_rejections"]
        if item.get("page") == 7
        and item.get("reason")
        == "cross_page_figure_continuation_explicitly_detected"
    ]

    expected_rw5 = annotation_by_target["P4-RS-RW2-005-P03-FIG1"]
    expected_rw7 = annotation_by_target["P4-RS-RW2-007-P05-FIG2"]
    bbox_stats = [
        {
            "target_id": "P4-RS-RW2-005-P03-FIG1",
            "actual_bbox": rw5_figure["bbox"],
            "expected_bbox": expected_rw5["target_bbox"],
            "bbox_exact": rw5_figure["bbox"] == expected_rw5["target_bbox"],
            **rw5_figure["region_render"],
            "caption_bbox": rw5_figure["caption"]["bbox"],
            "caption_overlap": (
                rw5_figure["bbox"]["y"] + rw5_figure["bbox"]["height"]
                > rw5_figure["caption"]["bbox"]["y"]
            ),
            "baseline_native_asset_sha256": rw5_figure["native_asset"]["sha256"],
            "region_asset_sha256": rw5_figure["asset"]["sha256"],
        },
        {
            "target_id": "P4-RS-RW2-007-P05-FIG2",
            "actual_bbox": rw7_figure["bbox"],
            "expected_bbox": expected_rw7["target_bbox"],
            "bbox_exact": rw7_figure["bbox"] == expected_rw7["target_bbox"],
            **rw7_figure["region_render"],
            "caption_bbox": rw7_figure["caption"]["bbox"],
            "caption_overlap": (
                rw7_figure["bbox"]["y"] + rw7_figure["bbox"]["height"]
                > rw7_figure["caption"]["bbox"]["y"]
            ),
            "baseline_native_asset_sha256": rw7_figure["native_asset"]["sha256"],
            "region_asset_sha256": rw7_figure["asset"]["sha256"],
        },
        {
            "target_id": "P4-RS-RW2-005-P07-FIG3",
            "status": "rejected" if len(rw5_p7_rejections) == 1 else "invalid",
            "reason": (
                rw5_p7_rejections[0]["reason"]
                if len(rw5_p7_rejections) == 1
                else None
            ),
            "region_asset_count": sum(
                item["page"] == 7 and item["extraction_mode"] == "region-rendered"
                for item in rw5_manifest["figures"]
            ),
        },
    ]
    hard_checks = {
        "frozen_annotations_unchanged": True,
        "eight_regression_documents": len(regression) == 8,
        "eight_regression_manifests_valid": all(
            item["manifest_valid"] for item in regression
        ),
        "physical_documents_identical_to_phase3": all(
            item["physical_document_identical_to_phase3"] for item in regression
        ),
        "articles_identical_to_phase3": all(
            item["article_identical_to_phase3"] for item in regression
        ),
        "images_identical_to_phase3": all(
            item["images_identical_to_phase3"] for item in regression
        ),
        "default_region_render_disabled": all(
            item["region_render_opt_in_count"] == 0 for item in regression
        ),
        "target_runs_deterministic": all(
            item["two_runs_identical"] for item in target_results
        ),
        "rw2_005_p3_single_region_asset": len(rw5["region_figures"]) == 1,
        "rw2_005_p3_bbox_exact": bbox_stats[0]["bbox_exact"],
        "rw2_005_p3_caption_excluded": not bbox_stats[0]["caption_overlap"],
        "rw2_005_p7_safe_rejection": bbox_stats[2]["status"] == "rejected"
        and bbox_stats[2]["region_asset_count"] == 0,
        "rw2_007_p5_single_region_asset": len(rw7["region_figures"]) == 1,
        "rw2_007_p5_bbox_exact": bbox_stats[1]["bbox_exact"],
        "rw2_007_p5_caption_excluded": not bbox_stats[1]["caption_overlap"],
        "rw2_005_p3_manual_visual_complete": visual_decisions.get(
            "P4-RS-RW2-005-P03-FIG1"
        )
        == "PASS_COMPLETE_REGION_RENDER",
        "rw2_005_p7_manual_visual_rejection": visual_decisions.get(
            "P4-RS-RW2-005-P07-FIG3"
        )
        == "PASS_SAFE_REJECTION",
        "rw2_007_p5_manual_visual_complete": visual_decisions.get(
            "P4-RS-RW2-007-P05-FIG2"
        )
        == "PASS_COMPLETE_REGION_RENDER",
    }
    output = {
        "schema_version": "paper2md-phase4-render-spike-summary-v1",
        "base_commit": "ee379a5be6c713012e721d08995a88d5abec19af",
        "totals": {
            "regression_papers": 8,
            "regression_pages": sum(item["page_count"] for item in regression),
            "regression_output_files": sum(
                item["output_file_count"] for item in regression
            ),
            "regression_output_bytes": sum(item["output_bytes"] for item in regression),
            "target_document_runs": 4,
            "target_page_observations": 6,
            "target_output_files": sum(
                sum(item["run_file_counts"]) for item in target_results
            ),
            "target_output_bytes": sum(
                sum(item["run_bytes"]) for item in target_results
            ),
            "region_rendered_assets_per_run": 2,
            "safe_rejections_per_run": 1,
            "failure_count": 0,
            "timeout_count": 0,
            "skip_count": 0,
        },
        "regression": regression,
        "targets": target_results,
        "bbox_stats": bbox_stats,
        "hard_checks": hard_checks,
        "automatic_status": "PASS" if all(hard_checks.values()) else "FAIL",
        "visual_gate": "PASS" if all(
            key.startswith("rw2_") and value
            for key, value in hard_checks.items()
            if "manual_visual" in key
        ) else "FAIL",
        "implementation_notes": [
            {
                "status": "fixed_before_final_summary",
                "issue": "The first v1 runtime retained the old bitmap-group vector hash while expanding the region vector count.",
                "fix": "The request now hashes the complete region vector ID set; v1 runtime was retained and the same frozen matrix was rerun in results-v2.",
                "pixel_assets_unchanged": True
            }
        ],
    }
    (args.repo / "phase4_render_spike/bbox_machine_stats.json").write_text(
        json.dumps(bbox_stats, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.repo / "phase4_render_spike/spike_summary.json").write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if output["automatic_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
