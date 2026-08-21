"""Candidate-relation visual review and deterministic FinalLayout compiler.

The visual model classifies and groups existing geometry; it never draws
boxes.  Deterministic code unions candidate boxes, materializes parent links,
and validates the resulting visual-direct FinalLayout.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import re
from typing import Any, Mapping

from .exceptions import ContractValidationError
from .layout_models import (
    FinalLayout,
    LayoutCandidate,
    LayoutTask,
    NormalizedBBox,
)
from .layout_review import (
    LAYOUT_REVIEW_PROMPT_VERSION,
    configure_layout_review_task,
    layout_task_content_roi,
    validate_layout_review,
)


VISUAL_RELATION_REVIEW_VERSION = "paperwright-visual-relation-review-v0.1"
VISUAL_RELATION_PROMPT_VERSION = "paperwright-visual-relations-prompt-v0.2"
SUPPORTED_VISUAL_RELATION_PROMPT_VERSIONS = frozenset(
    {
        "paperwright-visual-relations-prompt-v0.1",
        VISUAL_RELATION_PROMPT_VERSION,
    }
)
VISUAL_RELATION_TASK_FILENAME = "visual-relation-task.json"
VISUAL_RELATION_REVIEW_FILENAME = "visual-relation-review.json"
VISUAL_RELATION_OVERLAY_FILENAME = "candidate-overlay.png"

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_CLASSES = {"exclude", "text", "visual", "unknown"}
_ROLES = {
    "heading",
    "body",
    "figure",
    "table",
    "caption",
    "footnote",
    "header",
    "footer",
    "margin",
    "equation",
    "other",
    "unknown",
}
_TEXT_ROLES = {"heading", "body", "caption", "footnote"}
_VISUAL_ROLES = {"figure", "table", "equation"}
_EXCLUDE_ROLES = {"header", "footer", "margin"}
_TOP_FIELDS = {
    "contract_version",
    "source_sha256",
    "page",
    "task_sha256",
    "reviewer",
    "prompt_version",
    "groups",
    "discarded_candidate_ids",
    "warnings",
}
_GROUP_FIELDS = {
    "group_id",
    "candidate_ids",
    "content_class",
    "role",
    "order",
    "parent_group_id",
    "confidence",
}


def _canonical_json(value: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def build_visual_relation_task(
    task: LayoutTask,
    *,
    issues: tuple[Mapping[str, Any], ...] = (),
) -> LayoutTask:
    """Return a text-free candidate task for relationship judgment."""

    configured = configure_layout_review_task(task, "candidate-assisted")
    candidates = list(configured.candidates)
    covered_elements = {
        element_id
        for candidate in candidates
        for element_id in candidate.source_element_ids
    }
    known_ids = {item.candidate_id for item in candidates}
    anchor_ordinal = 0
    used_issue_ids: list[str] = []
    for issue in issues:
        if (
            issue.get("kind")
            not in {
                "caption_visual_binding",
                "cross_page_caption_visual_binding",
            }
            or issue.get("page_index") != task.page.page_index
            or not isinstance(issue.get("scope"), Mapping)
        ):
            continue
        scope = issue["scope"]
        element_ids = tuple(
            item
            for item in scope.get("element_ids", ())
            if isinstance(item, str) and item
        )
        bbox_value = scope.get("bbox")
        if (
            not element_ids
            or covered_elements.intersection(element_ids)
            or not isinstance(bbox_value, Mapping)
        ):
            continue
        anchor_ordinal += 1
        candidate_id = f"I{anchor_ordinal:03d}"
        while candidate_id in known_ids:
            anchor_ordinal += 1
            candidate_id = f"I{anchor_ordinal:03d}"
        issue_id = issue.get("issue_id")
        cross_page = (
            issue.get("kind") == "cross_page_caption_visual_binding"
        )
        candidates.append(
            LayoutCandidate(
                candidate_id=candidate_id,
                bbox=NormalizedBBox.from_dict(bbox_value),
                source_element_ids=element_ids,
                element_kinds=("text",),
                features={
                    "high_confidence_caption_kind": "figure",
                    "starts_with_figure": True,
                    "issue_anchor": True,
                    "cross_page_caption_anchor": cross_page,
                    "issue_id": issue_id if isinstance(issue_id, str) else "unknown",
                },
            )
        )
        known_ids.add(candidate_id)
        covered_elements.update(element_ids)
        if isinstance(issue_id, str):
            used_issue_ids.append(issue_id)
    metadata = dict(configured.metadata)
    metadata.update(
        {
            "review_protocol": VISUAL_RELATION_REVIEW_VERSION,
            "visual_geometry_authority": "deterministic-candidate-union",
            "candidate_policy": "relationship-review-accounting",
            "issue_anchor_ids": used_issue_ids,
        }
    )
    return replace(
        configured,
        candidates=tuple(candidates),
        overlay_filename=VISUAL_RELATION_OVERLAY_FILENAME,
        metadata=metadata,
    )


def visual_relation_review_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_visual_relation_review_json(value).encode("utf-8")
    ).hexdigest()


def validate_visual_relation_review(
    value: Mapping[str, Any],
    task: LayoutTask,
) -> None:
    if not isinstance(value, Mapping) or set(value) != _TOP_FIELDS:
        raise ContractValidationError("visual relation review 顶层字段非法")
    if value["contract_version"] != VISUAL_RELATION_REVIEW_VERSION:
        raise ContractValidationError("visual relation review 版本非法")
    if value["source_sha256"] != task.source_sha256:
        raise ContractValidationError("visual relation review source hash 不一致")
    if value["page"] != task.page.to_dict():
        raise ContractValidationError("visual relation review page 不一致")
    if value["task_sha256"] != task.deterministic_sha256():
        raise ContractValidationError("visual relation review task hash 不一致")
    if (
        not isinstance(value["reviewer"], str)
        or not value["reviewer"]
        or value["prompt_version"]
        not in SUPPORTED_VISUAL_RELATION_PROMPT_VERSIONS
    ):
        raise ContractValidationError("visual relation reviewer/prompt 非法")
    groups = value["groups"]
    discarded = value["discarded_candidate_ids"]
    warnings = value["warnings"]
    if (
        not isinstance(groups, list)
        or not isinstance(discarded, list)
        or len(discarded) != len(set(discarded))
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) or not item for item in warnings)
    ):
        raise ContractValidationError("visual relation groups/discarded/warnings 非法")

    candidates = {item.candidate_id: item for item in task.candidates}
    if any(item not in candidates for item in discarded):
        raise ContractValidationError("visual relation discard 引用未知候选")
    group_by_id: dict[str, Mapping[str, Any]] = {}
    assigned: set[str] = set()
    orders: list[int] = []
    for group in groups:
        if not isinstance(group, Mapping) or set(group) != _GROUP_FIELDS:
            raise ContractValidationError("visual relation group 字段非法")
        group_id = group["group_id"]
        candidate_ids = group["candidate_ids"]
        content_class = group["content_class"]
        role = group["role"]
        order = group["order"]
        parent = group["parent_group_id"]
        confidence = group["confidence"]
        if (
            not isinstance(group_id, str)
            or _ID.fullmatch(group_id) is None
            or group_id in group_by_id
            or not isinstance(candidate_ids, list)
            or not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(item not in candidates for item in candidate_ids)
            or assigned.intersection(candidate_ids)
            or content_class not in _CONTENT_CLASSES
            or role not in _ROLES
            or (parent is not None and (not isinstance(parent, str) or _ID.fullmatch(parent) is None))
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise ContractValidationError("visual relation group 内容非法")
        if content_class == "exclude":
            if order is not None or role not in _EXCLUDE_ROLES:
                raise ContractValidationError("exclude group role/order 非法")
        else:
            if type(order) is not int or order < 1:
                raise ContractValidationError("非 exclude group 必须有正整数 order")
            orders.append(order)
        if role in _EXCLUDE_ROLES and content_class != "exclude":
            raise ContractValidationError("排除 role 必须使用 exclude")
        if role in _TEXT_ROLES and content_class not in {"text", "unknown"}:
            raise ContractValidationError("文本 role 的 content_class 非法")
        if role in _VISUAL_ROLES and content_class not in {"visual", "unknown"}:
            raise ContractValidationError("视觉 role 的 content_class 非法")
        group_by_id[group_id] = group
        assigned.update(candidate_ids)

    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise ContractValidationError("visual relation order 必须连续")
    if assigned.intersection(discarded):
        raise ContractValidationError("candidate 不能同时 group 与 discard")
    if assigned.union(discarded) != set(candidates):
        missing = sorted(set(candidates) - assigned - set(discarded))
        raise ContractValidationError(
            "visual relation candidate accounting 不守恒; "
            f"missing={','.join(missing) or 'none'}"
        )

    for group_id, group in group_by_id.items():
        parent_id = group["parent_group_id"]
        if parent_id is None:
            cross_page_caption = (
                group["role"] == "caption"
                and any(
                    candidates[candidate_id].features.get(
                        "cross_page_caption_anchor"
                    )
                    is True
                    for candidate_id in group["candidate_ids"]
                )
            )
            if group["role"] == "caption" and not cross_page_caption:
                raise ContractValidationError("caption group 必须绑定 visual parent")
            continue
        if parent_id == group_id or parent_id not in group_by_id:
            raise ContractValidationError("visual relation parent 引用非法")
        parent = group_by_id[parent_id]
        if group["role"] != "caption" or parent["content_class"] != "visual":
            raise ContractValidationError("只允许 caption-of visual 关系")
    for start in group_by_id:
        seen: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in seen:
                raise ContractValidationError("visual relation parent 存在循环")
            seen.add(current)
            parent = group_by_id.get(current, {}).get("parent_group_id")
            current = parent if isinstance(parent, str) else None

    candidate_group = {
        candidate_id: group
        for group in groups
        for candidate_id in group["candidate_ids"]
    }
    for candidate_id, candidate in candidates.items():
        caption_kind = candidate.features.get("high_confidence_caption_kind")
        if caption_kind in {"figure", "table"}:
            group = candidate_group.get(candidate_id)
            if group is None or group["role"] != "caption":
                raise ContractValidationError("高置信 caption 必须保留为 caption group")
        if int(candidate.features.get("raster_region_count", 0) or 0) >= 3:
            group = candidate_group.get(candidate_id)
            if (
                group is None
                or group["content_class"] != "visual"
                or group["role"] not in {"figure", "table"}
            ):
                raise ContractValidationError("compound raster 必须保留为 Figure/Table")


def normalize_visual_relation_review(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Repair deterministic contract defects without changing model semantics.

    A role with exactly one concrete compatible content class can safely repair
    an explicitly conflicting redundant field (for example ``header`` implies
    ``exclude``).  ``unknown`` remains an intentional uncertainty for text and
    visual roles.  Candidate membership, grouping, roles, parents, and discard
    decisions remain
    untouched.  Missing or duplicate candidates therefore still fail validation
    and must be reconsidered by the reviewer.
    """

    normalized = dict(value)
    raw_groups = value.get("groups")
    if not isinstance(raw_groups, list) or not all(
        isinstance(item, Mapping) for item in raw_groups
    ):
        return normalized

    groups = [dict(item) for item in raw_groups]

    order_changed = False
    role_warnings: list[str] = []
    for group in groups:
        group_id = group.get("group_id")
        role = group.get("role")
        content_class = group.get("content_class")
        inferred_class: str | None = None
        if role in _EXCLUDE_ROLES:
            inferred_class = "exclude"
        elif role in _TEXT_ROLES and content_class in {"visual", "exclude"}:
            inferred_class = "text"
        elif role in _VISUAL_ROLES and content_class in {"text", "exclude"}:
            inferred_class = "visual"
        if inferred_class is not None and content_class != inferred_class:
            group["content_class"] = inferred_class
            role_warnings.append(
                "paperwright normalized relation role/class: "
                f"group={group_id} role={role} "
                f"content_class={content_class}->{inferred_class}"
            )

    ordered_indices: list[int] = []
    for index, group in enumerate(groups):
        if group.get("content_class") == "exclude":
            if group.get("order") is not None:
                group["order"] = None
                order_changed = True
        else:
            ordered_indices.append(index)

    def reading_key(index: int) -> tuple[int, int, int]:
        group = groups[index]
        order = group.get("order")
        valid_order = type(order) is int and order > 0
        return (
            0 if valid_order else 1,
            order if valid_order else 0,
            index,
        )

    for new_order, index in enumerate(
        sorted(ordered_indices, key=reading_key),
        start=1,
    ):
        if groups[index].get("order") != new_order:
            groups[index]["order"] = new_order
            order_changed = True

    normalized["groups"] = groups
    warnings = normalized.get("warnings")
    if isinstance(warnings, list) and all(
        isinstance(item, str) and item for item in warnings
    ):
        normalized_warnings = [*warnings, *role_warnings]
        if order_changed:
            # Keep this warning stable and separate from the auditable
            # role-derived repairs above.
            order_warning = "paperwright normalized relation reading orders"
            if order_warning not in normalized_warnings:
                normalized_warnings.append(order_warning)
        if normalized_warnings != warnings:
            normalized["warnings"] = normalized_warnings
    return normalized


def canonical_visual_relation_review_json(
    value: Mapping[str, Any],
    *,
    task: LayoutTask | None = None,
) -> str:
    if task is not None:
        validate_visual_relation_review(value, task)
    elif (
        not isinstance(value, Mapping)
        or value.get("contract_version") != VISUAL_RELATION_REVIEW_VERSION
        or not isinstance(value.get("source_sha256"), str)
        or _HASH.fullmatch(value["source_sha256"]) is None
    ):
        raise ContractValidationError("visual relation review 无法规范化")
    return _canonical_json(value)


def _union_bbox(task: LayoutTask, candidate_ids: list[str]) -> NormalizedBBox:
    by_id = {item.candidate_id: item for item in task.candidates}
    boxes = [by_id[item].bbox for item in candidate_ids]
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return NormalizedBBox(left, top, right - left, bottom - top)


def _clip_to_roi(
    bbox: NormalizedBBox,
    roi: NormalizedBBox,
) -> tuple[NormalizedBBox | None, bool]:
    left = max(bbox.x, roi.x)
    top = max(bbox.y, roi.y)
    right = min(bbox.right, roi.right)
    bottom = min(bbox.bottom, roi.bottom)
    if right <= left or bottom <= top:
        return None, True
    clipped = NormalizedBBox(left, top, right - left, bottom - top)
    return clipped, clipped != bbox


def compile_visual_relation_review(
    review: Mapping[str, Any],
    *,
    relation_task: LayoutTask,
    final_task: LayoutTask,
) -> dict[str, Any]:
    """Compile reviewed candidate relations into validated FinalLayout JSON."""

    validate_visual_relation_review(review, relation_task)
    if (
        relation_task.source_sha256 != final_task.source_sha256
        or relation_task.page != final_task.page
    ):
        raise ContractValidationError("relation task 与 final task 页面身份不一致")
    region_id_by_group = {
        item["group_id"]: f"r-{item['group_id']}" for item in review["groups"]
    }
    regions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    warnings = list(review["warnings"])
    content_roi = layout_task_content_roi(final_task)
    for index, group in enumerate(review["groups"], start=1):
        region_id = region_id_by_group[group["group_id"]]
        union_bbox = _union_bbox(relation_task, group["candidate_ids"])
        clipped = False
        if group["content_class"] != "exclude" and content_roi is not None:
            bounded_bbox, clipped = _clip_to_roi(union_bbox, content_roi)
            if bounded_bbox is None:
                raise ContractValidationError(
                    f"non-exclude relation group {group['group_id']} is "
                    "outside the confirmed Content ROI"
                )
            union_bbox = bounded_bbox
        bbox = union_bbox.to_dict()
        if clipped:
            warnings.append(
                f"region {region_id} candidate union clipped to confirmed "
                "Content ROI"
            )
        parent = group["parent_group_id"]
        regions.append(
            {
                "region_id": region_id,
                "bbox": bbox,
                "content_class": group["content_class"],
                "role": group["role"],
                "order": group["order"],
                "source_candidate_ids": [],
                "source_element_ids": [],
                "parent_region_id": (
                    region_id_by_group[parent] if parent is not None else None
                ),
                "confidence": group["confidence"],
            }
        )
        actions.append(
            {
                "action_id": f"a-add-{index}",
                "action": "add",
                "source_candidate_ids": [],
                "result_region_ids": [region_id],
                "bbox": bbox,
                "target_region_id": None,
                "reason": (
                    "deterministic union of reviewed candidate group clipped "
                    "to confirmed Content ROI"
                    if clipped
                    else "deterministic union of reviewed candidate group"
                ),
            }
        )
    for index, group in enumerate(review["groups"], start=1):
        parent = group["parent_group_id"]
        if parent is None:
            continue
        actions.append(
            {
                "action_id": f"a-caption-{index}",
                "action": "attach-caption",
                "source_candidate_ids": [],
                "result_region_ids": [region_id_by_group[group["group_id"]]],
                "bbox": None,
                "target_region_id": region_id_by_group[parent],
                "reason": "reviewed caption-of relation",
            }
        )
    layout = {
        "contract_version": "paperwright-final-layout-v0.1",
        "source_sha256": final_task.source_sha256,
        "page": final_task.page.to_dict(),
        "reviewer": f"paperwright-visual-relations/{review['reviewer']}",
        "prompt_version": LAYOUT_REVIEW_PROMPT_VERSION,
        "regions": regions,
        "actions": actions,
        "warnings": warnings,
    }
    validate_layout_review(FinalLayout.from_dict(layout), final_task)
    return layout


__all__ = [
    "VISUAL_RELATION_OVERLAY_FILENAME",
    "VISUAL_RELATION_PROMPT_VERSION",
    "SUPPORTED_VISUAL_RELATION_PROMPT_VERSIONS",
    "VISUAL_RELATION_REVIEW_FILENAME",
    "VISUAL_RELATION_REVIEW_VERSION",
    "VISUAL_RELATION_TASK_FILENAME",
    "build_visual_relation_task",
    "canonical_visual_relation_review_json",
    "compile_visual_relation_review",
    "normalize_visual_relation_review",
    "validate_visual_relation_review",
    "visual_relation_review_sha256",
]
