"""Versioned contracts for hybrid page-layout review.

The layout contracts deliberately contain geometry, compact features, and
provenance only.  They do not require OCR text or permit an AI reviewer to
rewrite document content.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .exceptions import ContractValidationError
from .models import BBox, Page

LAYOUT_TASK_VERSION = "paper2md-layout-task-v0.1"
RASTER_LAYOUT_TASK_VERSION = "paper2md-layout-task-v0.2"
LAYOUT_TASK_VERSIONS = frozenset(
    {LAYOUT_TASK_VERSION, RASTER_LAYOUT_TASK_VERSION}
)
FINAL_LAYOUT_VERSION = "paper2md-final-layout-v0.1"

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ELEMENT_KINDS = frozenset(
    {"text", "image", "vector", "raster", "link", "annotation"}
)
_FEATURE_SCALARS = (str, int, float, bool, type(None))
_ACTIONS = frozenset(
    {
        "keep",
        "merge",
        "split",
        "resize",
        "discard",
        "add",
        "reorder",
        "attach-caption",
    }
)
_CONTENT_CLASSES = frozenset({"exclude", "text", "visual", "unknown"})
_ROLES = frozenset(
    {
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
)


def _finite(value: float, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ContractValidationError(f"{field_name} 必须是有限数")
    return number


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ContractValidationError(
            f"{field_name} 必须匹配 {_ID.pattern}"
        )
    return value


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ContractValidationError(f"{field_name} 必须是 64 位十六进制")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} 非十六进制") from exc
    return value.lower()


def _safe_filename(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise ContractValidationError(f"{field_name} 必须是安全的单层文件名")
    return value


def _feature_map(
    value: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        _identifier(key, f"{field_name} key")
        if not isinstance(item, _FEATURE_SCALARS):
            raise ContractValidationError(
                f"{field_name}.{key} 只能是标量或 null"
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise ContractValidationError(
                f"{field_name}.{key} 必须是有限数"
            )
        result[key] = item
    return result


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


@dataclass(frozen=True)
class NormalizedBBox:
    """Top-left, y-down bbox normalized to a page's [0, 1] extent."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            object.__setattr__(
                self,
                name,
                _finite(getattr(self, name), f"normalized bbox {name}"),
            )
        if self.x < 0 or self.y < 0:
            raise ContractValidationError("normalized bbox x/y 不能为负数")
        if self.width <= 0 or self.height <= 0:
            raise ContractValidationError("normalized bbox 必须具有正面积")
        if self.right > 1 + 1e-9 or self.bottom > 1 + 1e-9:
            raise ContractValidationError("normalized bbox 超出 [0,1] 页面范围")

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def to_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedBBox":
        return cls(
            x=value["x"],
            y=value["y"],
            width=value["width"],
            height=value["height"],
        )

    @classmethod
    def from_pdf_bbox(
        cls,
        bbox: BBox,
        *,
        page_width: float,
        page_height: float,
    ) -> "NormalizedBBox":
        width = _finite(page_width, "page_width")
        height = _finite(page_height, "page_height")
        if width <= 0 or height <= 0:
            raise ContractValidationError("页面尺寸必须为正")
        if bbox.right > width + 1e-6 or bbox.bottom > height + 1e-6:
            raise ContractValidationError("PDF bbox 超出页面范围")
        return cls(
            x=bbox.x / width,
            y=bbox.y / height,
            width=bbox.width / width,
            height=bbox.height / height,
        )

    def to_pdf_bbox(self, *, page_width: float, page_height: float) -> BBox:
        width = _finite(page_width, "page_width")
        height = _finite(page_height, "page_height")
        if width <= 0 or height <= 0:
            raise ContractValidationError("页面尺寸必须为正")
        return BBox(
            self.x * width,
            self.y * height,
            self.width * width,
            self.height * height,
        )

    def to_pixel_box(
        self,
        *,
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int]:
        if image_width <= 0 or image_height <= 0:
            raise ContractValidationError("图像尺寸必须为正")
        left = max(0, min(image_width - 1, round(self.x * image_width)))
        top = max(0, min(image_height - 1, round(self.y * image_height)))
        right = max(left + 1, min(image_width, round(self.right * image_width)))
        bottom = max(top + 1, min(image_height, round(self.bottom * image_height)))
        return left, top, right - 1, bottom - 1


@dataclass(frozen=True)
class LayoutPage:
    page_index: int
    width: float
    height: float
    rotation: int
    coordinate_system: str = "top-left/pdf-point/y-down"

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _finite(self.width, "page width"))
        object.__setattr__(self, "height", _finite(self.height, "page height"))
        if self.page_index < 0:
            raise ContractValidationError("page_index 不能为负数")
        if self.width <= 0 or self.height <= 0:
            raise ContractValidationError("页面尺寸必须为正")
        if self.rotation not in {0, 90, 180, 270}:
            raise ContractValidationError("rotation 只能是 0/90/180/270")
        if self.coordinate_system != "top-left/pdf-point/y-down":
            raise ContractValidationError("不支持的坐标系")

    @classmethod
    def from_page(cls, page: Page) -> "LayoutPage":
        return cls(
            page_index=page.page_index,
            width=page.width,
            height=page.height,
            rotation=page.rotation,
            coordinate_system=page.coordinate_system,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "coordinate_system": self.coordinate_system,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutPage":
        return cls(
            page_index=value["page_index"],
            width=value["width"],
            height=value["height"],
            rotation=value["rotation"],
            coordinate_system=value["coordinate_system"],
        )


@dataclass(frozen=True)
class LayoutCandidate:
    candidate_id: str
    bbox: NormalizedBBox
    source_element_ids: tuple[str, ...] = ()
    element_kinds: tuple[str, ...] = ()
    features: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.candidate_id, "candidate_id")
        if len(set(self.source_element_ids)) != len(self.source_element_ids):
            raise ContractValidationError("source_element_ids 不能重复")
        for item in self.source_element_ids:
            _identifier(item, "source_element_id")
        if len(set(self.element_kinds)) != len(self.element_kinds):
            raise ContractValidationError("element_kinds 不能重复")
        unsupported = set(self.element_kinds) - _ELEMENT_KINDS
        if unsupported:
            raise ContractValidationError(
                f"不支持的 element_kinds: {sorted(unsupported)}"
            )
        object.__setattr__(
            self,
            "features",
            _feature_map(self.features, "candidate features"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "bbox": self.bbox.to_dict(),
            "source_element_ids": list(self.source_element_ids),
            "element_kinds": list(self.element_kinds),
            "features": self.features,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutCandidate":
        return cls(
            candidate_id=value["candidate_id"],
            bbox=NormalizedBBox.from_dict(value["bbox"]),
            source_element_ids=tuple(value.get("source_element_ids", ())),
            element_kinds=tuple(value.get("element_kinds", ())),
            features=dict(value.get("features", {})),
        )


@dataclass(frozen=True)
class LayoutSeparator:
    separator_id: str
    orientation: str
    bbox: NormalizedBBox
    adjacent_candidate_ids: tuple[str, str]
    features: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.separator_id, "separator_id")
        if self.orientation not in {"horizontal", "vertical"}:
            raise ContractValidationError(
                "separator orientation 必须是 horizontal 或 vertical"
            )
        if len(self.adjacent_candidate_ids) != 2:
            raise ContractValidationError("分隔带必须关联两个候选区块")
        if self.adjacent_candidate_ids[0] == self.adjacent_candidate_ids[1]:
            raise ContractValidationError("分隔带不能关联同一个候选区块")
        for item in self.adjacent_candidate_ids:
            _identifier(item, "adjacent_candidate_id")
        object.__setattr__(
            self,
            "features",
            _feature_map(self.features, "separator features"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "separator_id": self.separator_id,
            "orientation": self.orientation,
            "bbox": self.bbox.to_dict(),
            "adjacent_candidate_ids": list(self.adjacent_candidate_ids),
            "features": self.features,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutSeparator":
        adjacent = tuple(value["adjacent_candidate_ids"])
        return cls(
            separator_id=value["separator_id"],
            orientation=value["orientation"],
            bbox=NormalizedBBox.from_dict(value["bbox"]),
            adjacent_candidate_ids=adjacent,  # type: ignore[arg-type]
            features=dict(value.get("features", {})),
        )


@dataclass(frozen=True)
class LayoutTask:
    source_sha256: str
    page: LayoutPage
    candidate_generator_version: str
    feature_schema_version: str
    candidates: tuple[LayoutCandidate, ...]
    separators: tuple[LayoutSeparator, ...] = ()
    preview_filename: str = "page.png"
    overlay_filename: str = "overlay.png"
    metadata: dict[str, Any] = field(default_factory=dict)
    contract_version: str = LAYOUT_TASK_VERSION

    def __post_init__(self) -> None:
        if self.contract_version not in LAYOUT_TASK_VERSIONS:
            raise ContractValidationError("布局任务契约版本不匹配")
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "source_sha256"),
        )
        if not self.candidate_generator_version:
            raise ContractValidationError("candidate_generator_version 必填")
        if not self.feature_schema_version:
            raise ContractValidationError("feature_schema_version 必填")
        _safe_filename(self.preview_filename, "preview_filename")
        _safe_filename(self.overlay_filename, "overlay_filename")
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("candidate_id 必须在页面内唯一")
        separator_ids = [item.separator_id for item in self.separators]
        if len(separator_ids) != len(set(separator_ids)):
            raise ContractValidationError("separator_id 必须在页面内唯一")
        known = set(ids)
        for separator in self.separators:
            missing = set(separator.adjacent_candidate_ids) - known
            if missing:
                raise ContractValidationError(
                    f"分隔带引用未知候选区块: {sorted(missing)}"
                )
        _canonical_json(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_sha256": self.source_sha256,
            "page": self.page.to_dict(),
            "candidate_generator_version": self.candidate_generator_version,
            "feature_schema_version": self.feature_schema_version,
            "preview_filename": self.preview_filename,
            "overlay_filename": self.overlay_filename,
            "candidates": [item.to_dict() for item in self.candidates],
            "separators": [item.to_dict() for item in self.separators],
            "metadata": self.metadata,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def deterministic_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutTask":
        return cls(
            contract_version=value["contract_version"],
            source_sha256=value["source_sha256"],
            page=LayoutPage.from_dict(value["page"]),
            candidate_generator_version=value["candidate_generator_version"],
            feature_schema_version=value["feature_schema_version"],
            preview_filename=value.get("preview_filename", "page.png"),
            overlay_filename=value.get("overlay_filename", "overlay.png"),
            candidates=tuple(
                LayoutCandidate.from_dict(item)
                for item in value.get("candidates", ())
            ),
            separators=tuple(
                LayoutSeparator.from_dict(item)
                for item in value.get("separators", ())
            ),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class LayoutAction:
    action_id: str
    action: str
    source_candidate_ids: tuple[str, ...] = ()
    result_region_ids: tuple[str, ...] = ()
    bbox: NormalizedBBox | None = None
    target_region_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.action_id, "action_id")
        if self.action not in _ACTIONS:
            raise ContractValidationError(f"不支持的布局动作: {self.action}")
        if len(set(self.source_candidate_ids)) != len(
            self.source_candidate_ids
        ):
            raise ContractValidationError("布局动作的候选区块不能重复")
        if len(set(self.result_region_ids)) != len(self.result_region_ids):
            raise ContractValidationError("布局动作的结果区块不能重复")
        for item in self.source_candidate_ids:
            _identifier(item, "source_candidate_id")
        for item in self.result_region_ids:
            _identifier(item, "result_region_id")
        if self.target_region_id is not None:
            _identifier(self.target_region_id, "target_region_id")
        if self.action == "merge" and len(self.source_candidate_ids) < 2:
            raise ContractValidationError("merge 至少需要两个候选区块")
        if self.action == "split" and len(self.source_candidate_ids) != 1:
            raise ContractValidationError("split 必须指定一个候选区块")
        if self.action in {"keep", "discard", "reorder"} and len(
            self.source_candidate_ids
        ) != 1:
            raise ContractValidationError(
                f"{self.action} 必须指定一个候选区块"
            )
        if self.action == "add" and (
            self.source_candidate_ids or self.bbox is None
        ):
            raise ContractValidationError("add 不得引用候选区块且必须提供 bbox")
        if self.action == "resize" and (
            len(self.source_candidate_ids) != 1 or self.bbox is None
        ):
            raise ContractValidationError("resize 必须指定一个候选区块和 bbox")
        if self.action == "attach-caption" and self.target_region_id is None:
            raise ContractValidationError(
                "attach-caption 必须指定 target_region_id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "source_candidate_ids": list(self.source_candidate_ids),
            "result_region_ids": list(self.result_region_ids),
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "target_region_id": self.target_region_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutAction":
        bbox = value.get("bbox")
        return cls(
            action_id=value["action_id"],
            action=value["action"],
            source_candidate_ids=tuple(value.get("source_candidate_ids", ())),
            result_region_ids=tuple(value.get("result_region_ids", ())),
            bbox=NormalizedBBox.from_dict(bbox) if bbox is not None else None,
            target_region_id=value.get("target_region_id"),
            reason=value.get("reason"),
        )


@dataclass(frozen=True)
class LayoutRegion:
    region_id: str
    bbox: NormalizedBBox
    content_class: str
    role: str
    order: int | None
    source_candidate_ids: tuple[str, ...] = ()
    source_element_ids: tuple[str, ...] = ()
    parent_region_id: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _identifier(self.region_id, "region_id")
        if self.content_class not in _CONTENT_CLASSES:
            raise ContractValidationError(
                f"不支持的 content_class: {self.content_class}"
            )
        if self.role not in _ROLES:
            raise ContractValidationError(f"不支持的 role: {self.role}")
        if self.order is not None and self.order < 1:
            raise ContractValidationError("阅读顺序必须从 1 开始")
        if self.content_class != "exclude" and self.order is None:
            raise ContractValidationError("非排除区块必须提供阅读顺序")
        if self.content_class == "exclude" and self.order is not None:
            raise ContractValidationError("排除区块不得进入阅读顺序")
        if len(set(self.source_candidate_ids)) != len(
            self.source_candidate_ids
        ):
            raise ContractValidationError("区块的 source_candidate_ids 不能重复")
        if len(set(self.source_element_ids)) != len(self.source_element_ids):
            raise ContractValidationError("区块的 source_element_ids 不能重复")
        for item in self.source_candidate_ids:
            _identifier(item, "source_candidate_id")
        for item in self.source_element_ids:
            _identifier(item, "source_element_id")
        if self.parent_region_id is not None:
            _identifier(self.parent_region_id, "parent_region_id")
            if self.parent_region_id == self.region_id:
                raise ContractValidationError("区块不能以自身作为父区块")
        if self.confidence is not None:
            confidence = _finite(self.confidence, "confidence")
            if not 0 <= confidence <= 1:
                raise ContractValidationError("confidence 必须位于 [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "bbox": self.bbox.to_dict(),
            "content_class": self.content_class,
            "role": self.role,
            "order": self.order,
            "source_candidate_ids": list(self.source_candidate_ids),
            "source_element_ids": list(self.source_element_ids),
            "parent_region_id": self.parent_region_id,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayoutRegion":
        return cls(
            region_id=value["region_id"],
            bbox=NormalizedBBox.from_dict(value["bbox"]),
            content_class=value["content_class"],
            role=value["role"],
            order=value.get("order"),
            source_candidate_ids=tuple(value.get("source_candidate_ids", ())),
            source_element_ids=tuple(value.get("source_element_ids", ())),
            parent_region_id=value.get("parent_region_id"),
            confidence=value.get("confidence"),
        )


@dataclass(frozen=True)
class FinalLayout:
    source_sha256: str
    page: LayoutPage
    regions: tuple[LayoutRegion, ...]
    actions: tuple[LayoutAction, ...] = ()
    reviewer: str = "unknown"
    prompt_version: str = "unknown"
    warnings: tuple[str, ...] = ()
    contract_version: str = FINAL_LAYOUT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != FINAL_LAYOUT_VERSION:
            raise ContractValidationError("最终布局契约版本不匹配")
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "source_sha256"),
        )
        if not self.reviewer or not self.prompt_version:
            raise ContractValidationError("reviewer 与 prompt_version 必填")
        region_ids = [item.region_id for item in self.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ContractValidationError("region_id 必须在页面内唯一")
        action_ids = [item.action_id for item in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ContractValidationError("action_id 必须在页面内唯一")
        orders = [
            item.order
            for item in self.regions
            if item.content_class != "exclude"
        ]
        if len(orders) != len(set(orders)):
            raise ContractValidationError("非排除区块的阅读顺序不能重复")
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ContractValidationError("非排除区块的阅读顺序必须从 1 连续递增")
        known_regions = set(region_ids)
        parents = {
            item.region_id: item.parent_region_id
            for item in self.regions
            if item.parent_region_id is not None
        }
        missing_parents = set(parents.values()) - known_regions
        if missing_parents:
            raise ContractValidationError(
                f"parent_region_id 引用未知区块: {sorted(missing_parents)}"
            )
        for start in parents:
            seen: set[str] = set()
            current: str | None = start
            while current is not None:
                if current in seen:
                    raise ContractValidationError("区块父子关系存在循环")
                seen.add(current)
                current = parents.get(current)
        for action in self.actions:
            missing_results = set(action.result_region_ids) - known_regions
            if missing_results:
                raise ContractValidationError(
                    f"布局动作引用未知结果区块: {sorted(missing_results)}"
                )
            if (
                action.target_region_id is not None
                and action.target_region_id not in known_regions
            ):
                raise ContractValidationError(
                    f"布局动作引用未知目标区块: {action.target_region_id}"
                )
        for warning in self.warnings:
            if not isinstance(warning, str) or not warning:
                raise ContractValidationError("warnings 必须是非空字符串")

    def validate_against(self, task: LayoutTask) -> None:
        if self.source_sha256 != task.source_sha256:
            raise ContractValidationError("最终布局与任务 source_sha256 不一致")
        if self.page != task.page:
            raise ContractValidationError("最终布局与任务页面描述不一致")
        known_candidates = {item.candidate_id for item in task.candidates}
        for region in self.regions:
            missing = set(region.source_candidate_ids) - known_candidates
            if missing:
                raise ContractValidationError(
                    f"最终区块引用未知候选区块: {sorted(missing)}"
                )
        for action in self.actions:
            missing = set(action.source_candidate_ids) - known_candidates
            if missing:
                raise ContractValidationError(
                    f"布局动作引用未知候选区块: {sorted(missing)}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_sha256": self.source_sha256,
            "page": self.page.to_dict(),
            "reviewer": self.reviewer,
            "prompt_version": self.prompt_version,
            "regions": [item.to_dict() for item in self.regions],
            "actions": [item.to_dict() for item in self.actions],
            "warnings": list(self.warnings),
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def deterministic_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FinalLayout":
        return cls(
            contract_version=value["contract_version"],
            source_sha256=value["source_sha256"],
            page=LayoutPage.from_dict(value["page"]),
            reviewer=value.get("reviewer", "unknown"),
            prompt_version=value.get("prompt_version", "unknown"),
            regions=tuple(
                LayoutRegion.from_dict(item)
                for item in value.get("regions", ())
            ),
            actions=tuple(
                LayoutAction.from_dict(item)
                for item in value.get("actions", ())
            ),
            warnings=tuple(value.get("warnings", ())),
        )
