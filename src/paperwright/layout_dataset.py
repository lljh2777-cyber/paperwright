"""Deterministic numeric dataset export from reviewed layout bundles.

The exporter never reads page pixels or article text.  It converts validated
layout tasks and final reviews into compact JSONL records grouped by document,
so downstream train/validation/test splits can be made without page leakage.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .exceptions import ContractValidationError, OutputConflictError
from .layout_models import (
    FinalLayout,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from .layout_review import validate_layout_review

LAYOUT_DATASET_VERSION = "paperwright-layout-dataset-v0.1"

_STRUCTURAL_ACTIONS = frozenset(
    {"keep", "merge", "split", "resize", "discard", "reorder", "add"}
)


@dataclass(frozen=True)
class LayoutDatasetExportResult:
    output_dir: Path
    manifest: dict[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(_canonical_json(record))
            stream.write("\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox(value: NormalizedBBox) -> dict[str, float]:
    return value.to_dict()


def _axis_gap(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> float:
    return max(first_start - second_end, second_start - first_end, 0.0)


def _overlap_ratio(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> float:
    overlap = max(
        0.0,
        min(first_end, second_end) - max(first_start, second_start),
    )
    smaller = min(first_end - first_start, second_end - second_start)
    return overlap / smaller if smaller > 0 else 0.0


def _pair_geometry(
    first: NormalizedBBox,
    second: NormalizedBBox,
) -> dict[str, float]:
    first_center_x = first.x + first.width / 2
    first_center_y = first.y + first.height / 2
    second_center_x = second.x + second.width / 2
    second_center_y = second.y + second.height / 2
    return {
        "x_gap": _axis_gap(first.x, first.right, second.x, second.right),
        "y_gap": _axis_gap(first.y, first.bottom, second.y, second.bottom),
        "horizontal_overlap_ratio": _overlap_ratio(
            first.x,
            first.right,
            second.x,
            second.right,
        ),
        "vertical_overlap_ratio": _overlap_ratio(
            first.y,
            first.bottom,
            second.y,
            second.bottom,
        ),
        "center_dx": second_center_x - first_center_x,
        "center_dy": second_center_y - first_center_y,
    }


def _review_pairs(
    review_roots: Sequence[str | Path],
    output_dir: Path,
) -> list[tuple[Path, LayoutTask, FinalLayout]]:
    if not review_roots:
        raise ContractValidationError("至少需要一个 review root")
    resolved_output = output_dir.expanduser().resolve(strict=False)
    found: list[tuple[Path, LayoutTask, FinalLayout]] = []
    keys: set[tuple[str, int]] = set()
    for value in review_roots:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ContractValidationError(f"review root 不是目录: {root}")
        try:
            resolved_output.relative_to(root)
        except ValueError:
            pass
        else:
            raise OutputConflictError("dataset 输出目录不能位于 review root 内")

        task_paths = sorted(
            root.rglob("layout-task.json"),
            key=lambda item: item.as_posix(),
        )
        if not task_paths:
            raise ContractValidationError(f"review root 中没有 layout-task.json: {root}")
        for task_path in task_paths:
            layout_path = task_path.with_name("final-layout.json")
            if not layout_path.is_file():
                raise ContractValidationError(
                    f"缺少已复核 final-layout.json: {task_path.parent}"
                )
            task = LayoutTask.from_dict(
                json.loads(task_path.read_text(encoding="utf-8"))
            )
            layout = FinalLayout.from_dict(
                json.loads(layout_path.read_text(encoding="utf-8"))
            )
            validate_layout_review(layout, task)
            key = (task.source_sha256, task.page.page_index)
            if key in keys:
                raise ContractValidationError(
                    f"重复文档页面: sha256:{key[0]} page={key[1]}"
                )
            keys.add(key)
            found.append((task_path.parent, task, layout))
    return sorted(found, key=lambda item: (item[1].source_sha256, item[1].page.page_index))


def _candidate_records(
    task: LayoutTask,
    layout: FinalLayout,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    regions_by_candidate: dict[str, list[LayoutRegion]] = {
        item.candidate_id: [] for item in task.candidates
    }
    for region in layout.regions:
        for candidate_id in region.source_candidate_ids:
            regions_by_candidate[candidate_id].append(region)

    actions_by_candidate: dict[str, set[str]] = {
        item.candidate_id: set() for item in task.candidates
    }
    for action in layout.actions:
        if action.action not in _STRUCTURAL_ACTIONS:
            continue
        for candidate_id in action.source_candidate_ids:
            actions_by_candidate[candidate_id].add(action.action)

    labels: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    document_id = f"sha256:{task.source_sha256}"
    for candidate in sorted(task.candidates, key=lambda item: item.candidate_id):
        assigned = sorted(
            regions_by_candidate[candidate.candidate_id],
            key=lambda item: item.region_id,
        )
        structural_actions = sorted(actions_by_candidate[candidate.candidate_id])
        if not structural_actions:
            structural_actions = ["keep"] if assigned else ["discard"]
        one_to_one = len(assigned) == 1 and not (
            {"merge", "split"} & set(structural_actions)
        )
        label = assigned[0] if one_to_one else None
        common = {
            "schema_version": LAYOUT_DATASET_VERSION,
            "document_id": document_id,
            "page_index": task.page.page_index,
            "candidate_id": candidate.candidate_id,
            "candidate_bbox": _bbox(candidate.bbox),
            "candidate_features": candidate.features,
            "element_kinds": list(candidate.element_kinds),
            "task_sha256": task.deterministic_sha256(),
            "layout_sha256": layout.deterministic_sha256(),
        }
        labels.append(
            {
                **common,
                "content_class": label.content_class if label else None,
                "role": label.role if label else None,
                "final_bbox": _bbox(label.bbox) if label else None,
                "eligible_for_candidate_classifier": label is not None,
                "exclusion_reason": (
                    None
                    if label is not None
                    else (
                        "candidate_was_split_or_merged"
                        if assigned
                        else "candidate_was_discarded"
                    )
                ),
            }
        )
        actions.append(
            {
                **common,
                "layout_actions": structural_actions,
                "assigned_region_count": len(assigned),
                "assigned_region_ids": [item.region_id for item in assigned],
            }
        )
    return labels, actions


def _reading_order_records(
    task: LayoutTask,
    layout: FinalLayout,
) -> list[dict[str, Any]]:
    regions = sorted(
        (
            item
            for item in layout.regions
            if item.content_class != "exclude"
        ),
        key=lambda item: item.region_id,
    )
    result: list[dict[str, Any]] = []
    for index, first in enumerate(regions):
        for second in regions[index + 1 :]:
            result.append(
                {
                    "schema_version": LAYOUT_DATASET_VERSION,
                    "document_id": f"sha256:{task.source_sha256}",
                    "page_index": task.page.page_index,
                    "first_region_id": first.region_id,
                    "second_region_id": second.region_id,
                    "first_bbox": _bbox(first.bbox),
                    "second_bbox": _bbox(second.bbox),
                    "first_content_class": first.content_class,
                    "second_content_class": second.content_class,
                    "first_role": first.role,
                    "second_role": second.role,
                    "geometry": _pair_geometry(first.bbox, second.bbox),
                    "first_precedes_second": bool(first.order < second.order),
                }
            )
    return result


def _caption_pair_records(
    task: LayoutTask,
    layout: FinalLayout,
) -> list[dict[str, Any]]:
    captions = sorted(
        (item for item in layout.regions if item.role == "caption"),
        key=lambda item: item.region_id,
    )
    visuals = sorted(
        (item for item in layout.regions if item.content_class == "visual"),
        key=lambda item: item.region_id,
    )
    result: list[dict[str, Any]] = []
    for caption in captions:
        for visual in visuals:
            result.append(
                {
                    "schema_version": LAYOUT_DATASET_VERSION,
                    "document_id": f"sha256:{task.source_sha256}",
                    "page_index": task.page.page_index,
                    "caption_region_id": caption.region_id,
                    "visual_region_id": visual.region_id,
                    "caption_bbox": _bbox(caption.bbox),
                    "visual_bbox": _bbox(visual.bbox),
                    "visual_role": visual.role,
                    "geometry": _pair_geometry(visual.bbox, caption.bbox),
                    "is_attached": caption.parent_region_id == visual.region_id,
                }
            )
    return result


def export_layout_dataset(
    review_roots: Sequence[str | Path],
    output_dir: str | Path,
) -> LayoutDatasetExportResult:
    """Export validated reviews as source-free, deterministic JSONL datasets."""

    destination = Path(output_dir).expanduser().resolve(strict=False)
    if destination.exists():
        raise OutputConflictError(f"输出目录已存在: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pairs = _review_pairs(review_roots, destination)

    candidate_labels: list[dict[str, Any]] = []
    action_labels: list[dict[str, Any]] = []
    reading_pairs: list[dict[str, Any]] = []
    caption_pairs: list[dict[str, Any]] = []
    content_roi_labels: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    for _, task, layout in pairs:
        candidate_page, action_page = _candidate_records(task, layout)
        candidate_labels.extend(candidate_page)
        action_labels.extend(action_page)
        reading_pairs.extend(_reading_order_records(task, layout))
        caption_pairs.extend(_caption_pair_records(task, layout))
        roi = task.metadata.get("analysis_roi")
        if (
            isinstance(roi, dict)
            and isinstance(roi.get("bbox"), dict)
            and isinstance(roi.get("source"), str)
        ):
            content_roi_labels.append(
                {
                    "schema_version": LAYOUT_DATASET_VERSION,
                    "document_id": f"sha256:{task.source_sha256}",
                    "page_index": task.page.page_index,
                    "page_width": task.page.width,
                    "page_height": task.page.height,
                    "content_bbox": roi["bbox"],
                    "label_source": roi["source"],
                    "excluded_element_count": len(
                        task.metadata.get("excluded_element_ids", ())
                    ),
                    "boundary_crossing_element_count": len(
                        task.metadata.get(
                            "boundary_crossing_element_ids",
                            (),
                        )
                    ),
                    "destructive_crop": False,
                }
            )
        document_id = f"sha256:{task.source_sha256}"
        document = documents.setdefault(
            document_id,
            {
                "document_id": document_id,
                "page_indices": [],
                "candidate_generator_versions": set(),
                "feature_schema_versions": set(),
            },
        )
        document["page_indices"].append(task.page.page_index)
        document["candidate_generator_versions"].add(
            task.candidate_generator_version
        )
        document["feature_schema_versions"].add(task.feature_schema_version)

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.paperwright-dataset-",
            dir=destination.parent,
        )
    )
    try:
        files_and_records = (
            ("candidate_labels.jsonl", candidate_labels),
            ("action_labels.jsonl", action_labels),
            ("reading_order_pairs.jsonl", reading_pairs),
            ("caption_pairs.jsonl", caption_pairs),
            ("content_roi_labels.jsonl", content_roi_labels),
        )
        for filename, records in files_and_records:
            _write_jsonl(temporary / filename, records)

        file_hashes = {
            filename: _sha256(temporary / filename)
            for filename, _ in files_and_records
        }
        normalized_documents = []
        for document_id in sorted(documents):
            value = documents[document_id]
            normalized_documents.append(
                {
                    "document_id": document_id,
                    "page_indices": sorted(value["page_indices"]),
                    "candidate_generator_versions": sorted(
                        value["candidate_generator_versions"]
                    ),
                    "feature_schema_versions": sorted(
                        value["feature_schema_versions"]
                    ),
                }
            )
        manifest = {
            "schema_version": LAYOUT_DATASET_VERSION,
            "document_count": len(normalized_documents),
            "page_count": len(pairs),
            "record_counts": {
                "candidate_labels": len(candidate_labels),
                "action_labels": len(action_labels),
                "reading_order_pairs": len(reading_pairs),
                "caption_pairs": len(caption_pairs),
                "content_roi_labels": len(content_roi_labels),
            },
            "documents": normalized_documents,
            "split_unit": "document_id",
            "contains_article_text": False,
            "contains_page_images": False,
            "contains_source_element_ids": False,
            "file_sha256": file_hashes,
            "deterministic_content_sha256": hashlib.sha256(
                _canonical_json(file_hashes).encode("utf-8")
            ).hexdigest(),
        }
        (temporary / "dataset_manifest.json").write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, destination)
    except Exception:
        # The temporary directory contains only this fixed, flat file set.
        # Clean each known file explicitly rather than performing a recursive
        # or broad deletion.
        for filename in (
            "candidate_labels.jsonl",
            "action_labels.jsonl",
            "reading_order_pairs.jsonl",
            "caption_pairs.jsonl",
            "content_roi_labels.jsonl",
            "dataset_manifest.json",
        ):
            path = temporary / filename
            if path.is_file():
                path.unlink()
        if temporary.is_dir():
            temporary.rmdir()
        raise
    return LayoutDatasetExportResult(destination, manifest)
