"""Reviewable cross-page Figure/Table caption relations.

Page-local layout remains authoritative for geometry.  This contract exposes
only adjacent-page relationship candidates and lets a reviewer select a
caption-of edge; it cannot add text, draw boxes, or alter page layouts.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence

from .exceptions import ContractValidationError
from .layout_caption import CaptionBinding
from .layout_models import FinalLayout, LayoutRegion, NormalizedBBox
from .models import Page, PhysicalDocument


CROSS_PAGE_CAPTION_TASK_VERSION = "paperwright-cross-page-caption-task-v0.1"
CROSS_PAGE_CAPTION_REVIEW_VERSION = "paperwright-cross-page-caption-review-v0.1"
CROSS_PAGE_CAPTION_PROMPT_VERSION = "paperwright-cross-page-caption-prompt-v0.1"
CROSS_PAGE_CAPTION_TASK_FILENAME = "cross-page-caption-task.json"
CROSS_PAGE_CAPTION_REVIEW_FILENAME = "cross-page-caption-review.json"
CROSS_PAGE_CAPTION_USAGE_FILENAME = "cross-page-caption-usage.json"

CaptionTextResolver = Callable[[Page, LayoutRegion], str]
_HASH = re.compile(r"^[0-9a-f]{64}$")
_LABEL = re.compile(
    r"^\s*(?P<kind>fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?\s*(?:[|.:])",
    re.IGNORECASE,
)


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


def cross_page_caption_task_sha256(value: Mapping[str, Any]) -> str:
    validate_cross_page_caption_task(value)
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _ref(page_index: int, region_id: str) -> str:
    return f"p{page_index + 1:04d}:{region_id}"


def _kind(text: str) -> str | None:
    match = _LABEL.match(text)
    if match is None:
        return None
    return "table" if match.group("kind").casefold().startswith("table") else "figure"


def native_caption_text(page: Page, region: LayoutRegion) -> str:
    """Return compact native text without asking a model to transcribe it."""

    selected = set(region.source_element_ids)
    elements = sorted(
        (
            item
            for item in page.elements
            if item.element_id in selected and item.kind == "text" and item.text
        ),
        key=lambda item: (
            item.metadata.get("normalized_order", 1_000_000),
            item.bbox.y,
            item.bbox.x,
            item.element_id,
        ),
    )
    return " ".join(" ".join((item.text or "").split()) for item in elements)


def build_cross_page_caption_task(
    document: PhysicalDocument,
    layouts: Sequence[FinalLayout],
    *,
    caption_text: CaptionTextResolver,
) -> dict[str, Any]:
    if len(layouts) != len(document.pages):
        raise ValueError("cross-page caption layouts 与文档页数不一致")
    layout_hashes: list[dict[str, Any]] = []
    for page, layout in zip(document.pages, layouts, strict=True):
        if (
            layout.source_sha256 != document.source_sha256
            or layout.page.page_index != page.page_index
        ):
            raise ContractValidationError("cross-page caption 页面身份不一致")
        layout_hashes.append(
            {
                "page_index": page.page_index,
                "final_layout_sha256": layout.deterministic_sha256(),
            }
        )

    pairs: list[dict[str, Any]] = []
    for caption_page_index in range(1, len(layouts)):
        caption_page = document.pages[caption_page_index]
        caption_layout = layouts[caption_page_index]
        visual_layout = layouts[caption_page_index - 1]
        visuals = tuple(
            region
            for region in visual_layout.regions
            if region.content_class == "visual"
            and region.role in {"figure", "table"}
            and (
                region.bbox.bottom >= 0.68
                or region.bbox.width * region.bbox.height >= 0.35
            )
        )
        if not visuals:
            continue
        captions = tuple(
            region
            for region in caption_layout.regions
            if region.content_class == "text"
            and region.role == "caption"
            and region.bbox.y <= 0.30
            and (region.order or 999) <= 3
        )
        ordinal = 0
        for caption in captions:
            text = caption_text(caption_page, caption).strip()
            caption_kind = _kind(text)
            if caption_kind is None:
                continue
            candidates: list[dict[str, Any]] = []
            for visual in visuals:
                if visual.role != caption_kind:
                    continue
                area = visual.bbox.width * visual.bbox.height
                score = (
                    70.0
                    + min(area, 0.9) * 20.0
                    + max(0.0, visual.bbox.bottom - 0.68) * 10.0
                    - caption.bbox.y * 20.0
                )
                candidates.append(
                    {
                        "visual_ref": _ref(caption_page_index - 1, visual.region_id),
                        "page_index": caption_page_index - 1,
                        "region_id": visual.region_id,
                        "role": visual.role,
                        "bbox": visual.bbox.to_dict(),
                        "score": round(score, 6),
                    }
                )
            if not candidates:
                continue
            ordinal += 1
            candidates.sort(key=lambda item: (-item["score"], item["visual_ref"]))
            pairs.append(
                {
                    "pair_id": (
                        f"cp-p{caption_page_index:04d}-"
                        f"p{caption_page_index + 1:04d}-{ordinal:03d}"
                    ),
                    "caption": {
                        "caption_ref": _ref(caption_page_index, caption.region_id),
                        "page_index": caption_page_index,
                        "region_id": caption.region_id,
                        "kind": caption_kind,
                        "bbox": caption.bbox.to_dict(),
                        "text": text,
                    },
                    "visual_candidates": candidates,
                    "signals": [
                        "adjacent_pages",
                        "caption_at_next_page_top",
                        "visual_at_previous_page_bottom_or_large",
                    ],
                }
            )
    value = {
        "contract_version": CROSS_PAGE_CAPTION_TASK_VERSION,
        "source_sha256": document.source_sha256,
        "page_count": len(document.pages),
        "layout_hashes": layout_hashes,
        "pairs": pairs,
    }
    validate_cross_page_caption_task(value)
    return value


def validate_cross_page_caption_task(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "source_sha256",
        "page_count",
        "layout_hashes",
        "pairs",
    }:
        raise ContractValidationError("cross-page caption task 顶层字段非法")
    if (
        value["contract_version"] != CROSS_PAGE_CAPTION_TASK_VERSION
        or not isinstance(value["source_sha256"], str)
        or _HASH.fullmatch(value["source_sha256"]) is None
        or type(value["page_count"]) is not int
        or value["page_count"] <= 0
    ):
        raise ContractValidationError("cross-page caption task 身份非法")
    layout_hashes = value["layout_hashes"]
    if not isinstance(layout_hashes, list) or len(layout_hashes) != value["page_count"]:
        raise ContractValidationError("cross-page caption layout hashes 不完整")
    for expected, record in enumerate(layout_hashes):
        if (
            not isinstance(record, Mapping)
            or set(record) != {"page_index", "final_layout_sha256"}
            or record["page_index"] != expected
            or not isinstance(record["final_layout_sha256"], str)
            or _HASH.fullmatch(record["final_layout_sha256"]) is None
        ):
            raise ContractValidationError("cross-page caption layout hash 非法")
    pairs = value["pairs"]
    if not isinstance(pairs, list):
        raise ContractValidationError("cross-page caption pairs 必须是数组")
    pair_ids: set[str] = set()
    caption_refs: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, Mapping) or set(pair) != {
            "pair_id",
            "caption",
            "visual_candidates",
            "signals",
        }:
            raise ContractValidationError("cross-page caption pair 字段非法")
        pair_id = pair["pair_id"]
        caption = pair["caption"]
        candidates = pair["visual_candidates"]
        if (
            not isinstance(pair_id, str)
            or not pair_id
            or pair_id in pair_ids
            or not isinstance(caption, Mapping)
            or set(caption)
            != {"caption_ref", "page_index", "region_id", "kind", "bbox", "text"}
            or not isinstance(caption["caption_ref"], str)
            or caption["caption_ref"] in caption_refs
            or type(caption["page_index"]) is not int
            or not 1 <= caption["page_index"] < value["page_count"]
            or not isinstance(caption["region_id"], str)
            or not caption["region_id"]
            or caption["caption_ref"]
            != _ref(caption["page_index"], caption["region_id"])
            or caption["kind"] not in {"figure", "table"}
            or not isinstance(caption["text"], str)
            or not caption["text"]
            or _kind(caption["text"]) != caption["kind"]
        ):
            raise ContractValidationError("cross-page caption caption 非法")
        try:
            NormalizedBBox.from_dict(caption["bbox"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("cross-page caption bbox 非法") from exc
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(pair["signals"], list)
            or any(not isinstance(item, str) or not item for item in pair["signals"])
        ):
            raise ContractValidationError("cross-page caption candidates/signals 非法")
        visual_refs: set[str] = set()
        for candidate in candidates:
            if (
                not isinstance(candidate, Mapping)
                or set(candidate)
                != {"visual_ref", "page_index", "region_id", "role", "bbox", "score"}
                or not isinstance(candidate["visual_ref"], str)
                or candidate["visual_ref"] in visual_refs
                or candidate["page_index"] != caption["page_index"] - 1
                or not isinstance(candidate["region_id"], str)
                or candidate["visual_ref"]
                != _ref(candidate["page_index"], candidate["region_id"])
                or candidate["role"] != caption["kind"]
                or not isinstance(candidate["score"], (int, float))
                or isinstance(candidate["score"], bool)
            ):
                raise ContractValidationError("cross-page caption visual candidate 非法")
            try:
                NormalizedBBox.from_dict(candidate["bbox"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractValidationError("cross-page visual bbox 非法") from exc
            visual_refs.add(candidate["visual_ref"])
        pair_ids.add(pair_id)
        caption_refs.add(caption["caption_ref"])


def canonical_cross_page_caption_task_json(value: Mapping[str, Any]) -> str:
    validate_cross_page_caption_task(value)
    return _canonical(value)


def validate_cross_page_caption_review(
    value: Mapping[str, Any],
    task: Mapping[str, Any],
) -> None:
    validate_cross_page_caption_task(task)
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "source_sha256",
        "task_sha256",
        "reviewer",
        "prompt_version",
        "bindings",
        "rejected_caption_refs",
        "warnings",
    }:
        raise ContractValidationError("cross-page caption review 顶层字段非法")
    if (
        value["contract_version"] != CROSS_PAGE_CAPTION_REVIEW_VERSION
        or value["source_sha256"] != task["source_sha256"]
        or value["task_sha256"] != cross_page_caption_task_sha256(task)
        or not isinstance(value["reviewer"], str)
        or not value["reviewer"]
        or value["prompt_version"] != CROSS_PAGE_CAPTION_PROMPT_VERSION
    ):
        raise ContractValidationError("cross-page caption review 身份非法")
    bindings = value["bindings"]
    rejected = value["rejected_caption_refs"]
    warnings = value["warnings"]
    if (
        not isinstance(bindings, list)
        or not isinstance(rejected, list)
        or len(rejected) != len(set(rejected))
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) or not item for item in warnings)
    ):
        raise ContractValidationError("cross-page caption review 内容非法")
    pair_by_caption = {
        pair["caption"]["caption_ref"]: pair for pair in task["pairs"]
    }
    selected_captions: set[str] = set()
    selected_visuals: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != {
            "caption_ref",
            "visual_ref",
            "confidence",
        }:
            raise ContractValidationError("cross-page caption binding 字段非法")
        caption_ref = binding["caption_ref"]
        visual_ref = binding["visual_ref"]
        pair = pair_by_caption.get(caption_ref)
        allowed = {
            item["visual_ref"] for item in pair["visual_candidates"]
        } if pair is not None else set()
        if (
            not isinstance(caption_ref, str)
            or caption_ref in selected_captions
            or not isinstance(visual_ref, str)
            or visual_ref in selected_visuals
            or visual_ref not in allowed
            or not isinstance(binding["confidence"], (int, float))
            or isinstance(binding["confidence"], bool)
            or not 0.0 <= float(binding["confidence"]) <= 1.0
        ):
            raise ContractValidationError("cross-page caption binding 内容非法")
        selected_captions.add(caption_ref)
        selected_visuals.add(visual_ref)
    known_captions = set(pair_by_caption)
    if (
        any(item not in known_captions for item in rejected)
        or selected_captions.intersection(rejected)
        or selected_captions.union(rejected) != known_captions
    ):
        raise ContractValidationError("cross-page caption review 未完整核算候选")


def canonical_cross_page_caption_review_json(
    value: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
) -> str:
    validate_cross_page_caption_review(value, task)
    return _canonical(value)


def empty_cross_page_caption_review(task: Mapping[str, Any]) -> dict[str, Any]:
    validate_cross_page_caption_task(task)
    if task["pairs"]:
        raise ValueError("非空 cross-page caption task 不能自动生成空 review")
    value = {
        "contract_version": CROSS_PAGE_CAPTION_REVIEW_VERSION,
        "source_sha256": task["source_sha256"],
        "task_sha256": cross_page_caption_task_sha256(task),
        "reviewer": "paperwright-no-cross-page-candidates",
        "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
        "bindings": [],
        "rejected_caption_refs": [],
        "warnings": [],
    }
    validate_cross_page_caption_review(value, task)
    return value


def compile_cross_page_caption_review(
    review: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
) -> tuple[tuple[CaptionBinding, ...], frozenset[tuple[int, str]]]:
    validate_cross_page_caption_review(review, task)
    pair_by_caption = {
        pair["caption"]["caption_ref"]: pair for pair in task["pairs"]
    }
    bindings: list[CaptionBinding] = []
    for item in review["bindings"]:
        pair = pair_by_caption[item["caption_ref"]]
        caption = pair["caption"]
        visual = next(
            candidate
            for candidate in pair["visual_candidates"]
            if candidate["visual_ref"] == item["visual_ref"]
        )
        bindings.append(
            CaptionBinding(
                caption_page_index=caption["page_index"],
                caption_region_id=caption["region_id"],
                visual_page_index=visual["page_index"],
                visual_region_id=visual["region_id"],
                method="reviewed_cross_page_relation",
                score=float(item["confidence"]) * 100.0,
            )
        )
    rejected = frozenset(
        (
            pair_by_caption[caption_ref]["caption"]["page_index"],
            pair_by_caption[caption_ref]["caption"]["region_id"],
        )
        for caption_ref in review["rejected_caption_refs"]
    )
    return tuple(bindings), rejected


__all__ = [
    "CROSS_PAGE_CAPTION_PROMPT_VERSION",
    "CROSS_PAGE_CAPTION_REVIEW_FILENAME",
    "CROSS_PAGE_CAPTION_REVIEW_VERSION",
    "CROSS_PAGE_CAPTION_TASK_FILENAME",
    "CROSS_PAGE_CAPTION_TASK_VERSION",
    "CROSS_PAGE_CAPTION_USAGE_FILENAME",
    "build_cross_page_caption_task",
    "canonical_cross_page_caption_review_json",
    "canonical_cross_page_caption_task_json",
    "compile_cross_page_caption_review",
    "cross_page_caption_task_sha256",
    "empty_cross_page_caption_review",
    "native_caption_text",
    "validate_cross_page_caption_review",
    "validate_cross_page_caption_task",
]
