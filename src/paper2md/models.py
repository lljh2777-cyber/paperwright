"""Backend-neutral PhysicalDocument data model."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .exceptions import ContractValidationError

CONTRACT_VERSION = "paper2md-physical-document-v0.2"


def _finite(value: float, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ContractValidationError(f"{field_name} 必须是有限数")
    return number


@dataclass(frozen=True)
class BBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.x < 0 or self.y < 0:
            raise ContractValidationError("bbox x/y 不能为负数")
        if self.width <= 0 or self.height <= 0:
            raise ContractValidationError("bbox 必须具有正面积")

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
    def from_dict(cls, value: dict[str, Any]) -> "BBox":
        return cls(
            x=value["x"],
            y=value["y"],
            width=value["width"],
            height=value["height"],
        )


@dataclass(frozen=True)
class Provenance:
    backend: str
    method: str
    source_ref: str
    confidence: float | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.backend or not self.method or not self.source_ref:
            raise ContractValidationError("provenance backend/method/source_ref 必填")
        if self.confidence is not None:
            value = _finite(self.confidence, "confidence")
            if not 0 <= value <= 1:
                raise ContractValidationError("confidence 必须位于 [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "method": self.method,
            "source_ref": self.source_ref,
            "confidence": self.confidence,
            "unavailable_reason": self.unavailable_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Provenance":
        return cls(
            backend=value["backend"],
            method=value["method"],
            source_ref=value["source_ref"],
            confidence=value.get("confidence"),
            unavailable_reason=value.get("unavailable_reason"),
        )


@dataclass(frozen=True)
class Element:
    element_id: str
    kind: str
    page_index: int
    bbox: BBox
    provenance: Provenance
    text: str | None = None
    source_object_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.element_id:
            raise ContractValidationError("element_id 必填")
        if self.kind not in {"text", "image", "vector", "link", "annotation"}:
            raise ContractValidationError(f"不支持的元素类型: {self.kind}")
        if self.page_index < 0:
            raise ContractValidationError("page_index 不能为负数")
        if self.text is not None:
            object.__setattr__(self, "text", unicodedata.normalize("NFC", self.text))

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "kind": self.kind,
            "page_index": self.page_index,
            "bbox": self.bbox.to_dict(),
            "text": self.text,
            "source_object_id": self.source_object_id,
            "provenance": self.provenance.to_dict(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Element":
        return cls(
            element_id=value["element_id"],
            kind=value["kind"],
            page_index=value["page_index"],
            bbox=BBox.from_dict(value["bbox"]),
            text=value.get("text"),
            source_object_id=value.get("source_object_id"),
            provenance=Provenance.from_dict(value["provenance"]),
            metadata=value.get("metadata", {}),
        )


@dataclass(frozen=True)
class Page:
    page_index: int
    width: float
    height: float
    rotation: int
    elements: tuple[Element, ...] = ()
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
        for element in self.elements:
            if element.page_index != self.page_index:
                raise ContractValidationError("元素 page_index 与所属页面不一致")
            if element.bbox.right > self.width + 1e-6:
                raise ContractValidationError("元素 bbox 超出页面右边界")
            if element.bbox.bottom > self.height + 1e-6:
                raise ContractValidationError("元素 bbox 超出页面下边界")

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "coordinate_system": self.coordinate_system,
            "elements": [element.to_dict() for element in self.elements],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Page":
        return cls(
            page_index=value["page_index"],
            width=value["width"],
            height=value["height"],
            rotation=value["rotation"],
            coordinate_system=value["coordinate_system"],
            elements=tuple(Element.from_dict(item) for item in value["elements"]),
        )


@dataclass(frozen=True)
class PhysicalDocument:
    source_sha256: str
    backend: str
    backend_version: str
    pages: tuple[Page, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractValidationError("PhysicalDocument 契约版本不匹配")
        if len(self.source_sha256) != 64:
            raise ContractValidationError("source_sha256 必须是 64 位十六进制")
        try:
            int(self.source_sha256, 16)
        except ValueError as exc:
            raise ContractValidationError("source_sha256 非十六进制") from exc
        if not self.backend or not self.backend_version:
            raise ContractValidationError("backend 与 backend_version 必填")
        if not self.pages:
            raise ContractValidationError("文档至少包含一页")
        if [page.page_index for page in self.pages] != list(range(len(self.pages))):
            raise ContractValidationError("页面索引必须从 0 连续递增")
        ids: set[str] = set()
        for page in self.pages:
            for element in page.elements:
                if element.element_id in ids:
                    raise ContractValidationError("element_id 必须在文档内唯一")
                ids.add(element.element_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_sha256": self.source_sha256,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "page_count": len(self.pages),
            "metadata": self.metadata,
            "pages": [page.to_dict() for page in self.pages],
        }

    def canonical_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )

    def deterministic_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PhysicalDocument":
        document = cls(
            contract_version=value["contract_version"],
            source_sha256=value["source_sha256"],
            backend=value["backend"],
            backend_version=value["backend_version"],
            metadata=value.get("metadata", {}),
            pages=tuple(Page.from_dict(item) for item in value["pages"]),
        )
        if value.get("page_count") != len(document.pages):
            raise ContractValidationError("page_count 与 pages 长度不一致")
        return document
