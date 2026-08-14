"""AI review protocol and deterministic validation for layout tasks."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .exceptions import ContractValidationError
from .layout_models import FinalLayout, LayoutTask, NormalizedBBox

LAYOUT_REVIEW_PROMPT_VERSION = "paperwright-layout-review-prompt-v0.4"
LAYOUT_REVIEW_MODES = frozenset({"visual-direct", "candidate-assisted"})
ROI_BOUNDARY_TOLERANCE = 0.002


def layout_task_content_roi(task: LayoutTask) -> NormalizedBBox | None:
    """Return the coarse semantic-content guard carried by a layout task."""

    value = task.metadata.get("analysis_roi")
    if not isinstance(value, dict) or not isinstance(value.get("bbox"), dict):
        return None
    return NormalizedBBox.from_dict(value["bbox"])


def _bbox_within_roi(
    bbox: NormalizedBBox,
    roi: NormalizedBBox,
) -> bool:
    tolerance = ROI_BOUNDARY_TOLERANCE
    return (
        bbox.x >= roi.x - tolerance
        and bbox.y >= roi.y - tolerance
        and bbox.right <= roi.right + tolerance
        and bbox.bottom <= roi.bottom + tolerance
    )


def configure_layout_review_task(
    task: LayoutTask,
    review_mode: str,
) -> LayoutTask:
    """Configure how a task is presented to the external reviewer."""

    if review_mode not in LAYOUT_REVIEW_MODES:
        raise ValueError(
            "review_mode must be visual-direct or candidate-assisted"
        )
    metadata = dict(task.metadata)
    metadata.update(
        {
            "review_mode": review_mode,
            "visual_geometry_authority": (
                "reviewer-page-image"
                if review_mode == "visual-direct"
                else "candidate-assisted"
            ),
            "candidate_policy": (
                "omitted-from-review-task"
                if review_mode == "visual-direct"
                else "required-review-accounting"
            ),
        }
    )
    if review_mode == "visual-direct":
        metadata = {
            "ocr_used": bool(task.metadata.get("ocr_used", False)),
            "review_mode": review_mode,
            "visual_geometry_authority": "reviewer-page-image",
            "candidate_policy": "omitted-from-review-task",
        }
        analysis_roi = task.metadata.get("analysis_roi")
        if isinstance(analysis_roi, dict):
            metadata["analysis_roi"] = analysis_roi
        raster_evidence = task.metadata.get("raster_evidence")
        if isinstance(raster_evidence, dict):
            metadata["raster_evidence"] = raster_evidence
    return replace(
        task,
        candidates=() if review_mode == "visual-direct" else task.candidates,
        separators=() if review_mode == "visual-direct" else task.separators,
        metadata=metadata,
    )


def build_layout_review_instructions(task: LayoutTask) -> str:
    """Build concise instructions for a visual AI layout reviewer."""

    review_mode = str(
        task.metadata.get("review_mode", "candidate-assisted")
    )
    if review_mode == "visual-direct":
        workflow = f"""## Visual-direct workflow

- Treat `{task.preview_filename}` as the only authority for final geometry.
- `{task.overlay_filename}` intentionally contains no rule-generated boxes.
- Use `content-roi.png` and `metadata.analysis_roi` as the coarse semantic-
  content guard. Every non-exclude region must stay inside that confirmed ROI.
- Content outside the ROI is page furniture by default. Adjust and reconfirm
  the ROI instead of silently importing outside content when the proposal is
  too narrow.
- The review task intentionally contains no rule-generated candidates or
  separators. Decide every final boundary from the page image itself.
- Draw every final logical block directly in normalized page coordinates.
- For a directly drawn block, create an `add` action whose `bbox` exactly
  matches the result region bbox; leave `source_candidate_ids` empty.
- Put all panels, axes, legends, and labels of one Figure in one visual region.
- Draw caption blocks separately, including all columns/fragments belonging to
  one caption. Set `parent_region_id` and add `attach-caption`.
- Explicitly draw headers, footers, and page numbers as `exclude` regions when
  needed. PaperWright assigns native PDF elements by geometry after review.
"""
    else:
        workflow = """## Candidate-assisted workflow

- Use `overlay.png` and candidate features as review evidence.
- Account for every candidate through assignment, split, or discard.
- Candidate geometry is provisional; merge, split, resize, or add regions when
  the page image contradicts it.
- Follow high-confidence `semantic_review_hints` unless visual evidence clearly
  contradicts them.
"""
    return f"""# PaperWright visual layout review

Contract: `{task.contract_version}`
Task SHA-256: `{task.deterministic_sha256()}`
Page index: `{task.page.page_index}`
Review mode: `{review_mode}`
Prompt version: `{LAYOUT_REVIEW_PROMPT_VERSION}`

## Inputs

- `{task.preview_filename}`: original full-page preview.
- `{task.overlay_filename}`: optional review overlay.
- `layout-task.json`: page coordinates and review contract metadata.

{workflow}
## Output rules

- Save only `paperwright-final-layout-v0.1` JSON as `final-layout.json`.
- Copy `source_sha256` and `page`; record the real reviewer/model name; set
  `prompt_version` to `{LAYOUT_REVIEW_PROMPT_VERSION}`.
- Never transcribe, rewrite, summarize, or invent article text.
- Never fill or guess `source_element_ids`; always output an empty array.
- Do not generate Markdown or perform OCR/image-text transcription.
- Set `content_class`, `role`, consecutive reading `order`, and parent links.
- Use `unknown` and retain the region when visual semantics are uncertain.
"""


def write_layout_review_instructions(
    output_path: str | Path,
    task: LayoutTask,
) -> Path:
    destination = Path(output_path)
    destination.write_text(
        build_layout_review_instructions(task),
        encoding="utf-8",
        newline="\n",
    )
    return destination


def _semantic_role_validation(layout: FinalLayout) -> None:
    text_roles = {"heading", "body", "caption", "footnote"}
    visual_roles = {"figure", "table", "equation"}
    exclude_roles = {"header", "footer", "margin"}
    by_id = {item.region_id: item for item in layout.regions}
    for region in layout.regions:
        if region.role in text_roles and region.content_class not in {
            "text",
            "unknown",
        }:
            raise ContractValidationError(
                f"{region.role} 必须是 text 或 unknown"
            )
        if region.role in visual_roles and region.content_class not in {
            "visual",
            "unknown",
        }:
            raise ContractValidationError(
                f"{region.role} 必须是 visual 或 unknown"
            )
        if region.role in exclude_roles and region.content_class != "exclude":
            raise ContractValidationError(
                f"{region.role} 必须使用 exclude"
            )
        if region.role == "caption" and region.parent_region_id is not None:
            parent = by_id[region.parent_region_id]
            if parent.content_class != "visual":
                raise ContractValidationError(
                    "caption 的父区块必须是 visual"
                )


def _semantic_evidence_validation(
    layout: FinalLayout,
    task: LayoutTask,
    assignments: Mapping[str, list[str]],
    discarded: set[str],
) -> None:
    if task.metadata.get("review_mode") == "visual-direct":
        return
    candidates_by_id = {
        item.candidate_id: item for item in task.candidates
    }
    regions_by_id = {item.region_id: item for item in layout.regions}
    for candidate_id, candidate in candidates_by_id.items():
        caption_kind = candidate.features.get(
            "high_confidence_caption_kind"
        )
        if caption_kind in {"figure", "table"}:
            if candidate_id in discarded:
                raise ContractValidationError(
                    f"high-confidence caption candidate {candidate_id} "
                    "cannot be discarded"
                )
            assigned_regions = [
                regions_by_id[item] for item in assignments[candidate_id]
            ]
            if not assigned_regions or any(
                item.role != "caption" for item in assigned_regions
            ):
                raise ContractValidationError(
                    f"high-confidence caption candidate {candidate_id} "
                    "must be assigned to a caption region"
                )
        if int(candidate.features.get("raster_region_count", 0) or 0) >= 3:
            assigned_regions = [
                regions_by_id[item] for item in assignments[candidate_id]
            ]
            if any(
                item.content_class != "visual"
                or item.role not in {"figure", "table"}
                for item in assigned_regions
            ):
                raise ContractValidationError(
                    f"compound raster candidate {candidate_id} must remain "
                    "a Figure/Table visual unless explicitly split"
                )

    hints = task.metadata.get("semantic_review_hints", ())
    if not isinstance(hints, (list, tuple)):
        raise ContractValidationError("semantic_review_hints must be a list")
    for hint in hints:
        if (
            not isinstance(hint, dict)
            or hint.get("kind") != "captioned_visual"
            or hint.get("confidence") != "high"
        ):
            continue
        caption_candidate_ids = tuple(
            item
            for item in hint.get("caption_candidate_ids", ())
            if isinstance(item, str)
        )
        visual_candidate_ids = tuple(
            item
            for item in hint.get("visual_candidate_ids", ())
            if isinstance(item, str)
        )
        caption_region_ids = {
            region_id
            for candidate_id in caption_candidate_ids
            for region_id in assignments.get(candidate_id, ())
        }
        visual_region_ids = {
            region_id
            for candidate_id in visual_candidate_ids
            for region_id in assignments.get(candidate_id, ())
        }
        label = hint.get("hint_id", "semantic hint")
        if len(caption_region_ids) != 1:
            raise ContractValidationError(
                f"{label} requires one merged caption region"
            )
        if len(visual_region_ids) != 1:
            raise ContractValidationError(
                f"{label} requires one merged visual region"
            )
        caption_region_id = next(iter(caption_region_ids))
        visual_region_id = next(iter(visual_region_ids))
        caption_region = regions_by_id[caption_region_id]
        visual_region = regions_by_id[visual_region_id]
        if (
            caption_region.role != "caption"
            or visual_region.content_class != "visual"
            or visual_region.role != hint.get("visual_role")
        ):
            raise ContractValidationError(
                f"{label} Figure/Table and caption roles do not match "
                "the task evidence"
            )
        if caption_region.parent_region_id != visual_region_id:
            raise ContractValidationError(
                f"caption region {caption_region_id} must reference visual "
                f"parent {visual_region_id}"
            )
        if not any(
            action.action == "attach-caption"
            and action.target_region_id == visual_region_id
            and (
                caption_region_id in action.result_region_ids
                or bool(
                    set(action.source_candidate_ids)
                    & set(caption_candidate_ids)
                )
            )
            for action in layout.actions
        ):
            raise ContractValidationError(
                f"caption region {caption_region_id} requires an "
                "attach-caption action"
            )


def validate_layout_review(
    layout: FinalLayout,
    task: LayoutTask,
) -> None:
    """Validate AI review completeness, provenance, and semantic consistency."""

    layout.validate_against(task)
    if layout.prompt_version != LAYOUT_REVIEW_PROMPT_VERSION:
        raise ContractValidationError("AI 布局审查 prompt_version 不匹配")
    _semantic_role_validation(layout)
    review_mode = str(
        task.metadata.get("review_mode", "candidate-assisted")
    )
    if review_mode not in LAYOUT_REVIEW_MODES:
        raise ContractValidationError("unsupported layout review mode")
    content_roi = layout_task_content_roi(task)
    if review_mode == "visual-direct" and content_roi is None:
        raise ContractValidationError(
            "visual-direct task requires a Content ROI"
        )
    if review_mode == "visual-direct":
        roi_value = task.metadata.get("analysis_roi")
        roi_source = (
            roi_value.get("source") if isinstance(roi_value, dict) else None
        )
        if not isinstance(roi_source, str) or not roi_source.startswith(
            "confirmed:"
        ):
            raise ContractValidationError(
                "visual-direct task requires a confirmed Content ROI"
            )
    if content_roi is not None:
        for region in layout.regions:
            if region.content_class == "exclude":
                continue
            if not _bbox_within_roi(region.bbox, content_roi):
                raise ContractValidationError(
                    f"non-exclude region {region.region_id} is outside "
                    "the confirmed Content ROI"
                )

    known_candidates = {item.candidate_id for item in task.candidates}
    assignments: dict[str, list[str]] = {
        candidate_id: [] for candidate_id in known_candidates
    }
    for region in layout.regions:
        for candidate_id in region.source_candidate_ids:
            assignments[candidate_id].append(region.region_id)
        if region.source_element_ids:
            raise ContractValidationError(
                f"{region.region_id} 的 source_element_ids 必须由程序生成"
            )

    discarded: set[str] = set()
    split_candidates: set[str] = set()
    for action in layout.actions:
        if action.action == "discard":
            discarded.update(action.source_candidate_ids)
        elif action.action == "split":
            split_candidates.update(action.source_candidate_ids)

    for candidate_id, region_ids in assignments.items():
        if region_ids and candidate_id in discarded:
            raise ContractValidationError(
                f"{candidate_id} 不能同时被分配和 discard"
            )
        if (
            review_mode == "candidate-assisted"
            and not region_ids
            and candidate_id not in discarded
        ):
            raise ContractValidationError(
                f"{candidate_id} 未被最终区块引用或 discard"
            )
        if len(region_ids) > 1 and candidate_id not in split_candidates:
            raise ContractValidationError(
                f"{candidate_id} 被多个区块引用但没有 split 动作"
            )

    if review_mode == "visual-direct":
        add_actions_by_region: dict[str, list[Any]] = {}
        for action in layout.actions:
            if action.action != "add":
                continue
            for region_id in action.result_region_ids:
                add_actions_by_region.setdefault(region_id, []).append(action)
        for region in layout.regions:
            if region.source_candidate_ids:
                continue
            actions = add_actions_by_region.get(region.region_id, [])
            if len(actions) != 1:
                raise ContractValidationError(
                    f"visual-direct region {region.region_id} requires "
                    "exactly one add action"
                )
            if actions[0].bbox != region.bbox:
                raise ContractValidationError(
                    f"visual-direct add bbox must match region "
                    f"{region.region_id} bbox"
                )

    _semantic_evidence_validation(layout, task, assignments, discarded)


def load_and_validate_layout_review(
    layout_json: str | Path,
    task_json: str | Path,
) -> FinalLayout:
    task_value: Mapping[str, Any] = json.loads(
        Path(task_json).read_text(encoding="utf-8")
    )
    layout_value: Mapping[str, Any] = json.loads(
        Path(layout_json).read_text(encoding="utf-8")
    )
    task = LayoutTask.from_dict(task_value)
    layout = FinalLayout.from_dict(layout_value)
    validate_layout_review(layout, task)
    return layout
