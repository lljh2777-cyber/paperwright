"""Deterministic manifest construction and bootstrap contract validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import ContractValidationError

MANIFEST_VERSION = "paper2md-manifest-v0.4"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class OutputFile:
    path: str
    role: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def build_manifest(
    *,
    source_sha256: str,
    backend: str,
    backend_version: str,
    contract_version: str,
    page_count: int,
    status: str,
    outputs: list[OutputFile],
    warnings: list[dict[str, Any]] | None = None,
    elements: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
    figures: list[dict[str, Any]] | None = None,
    figure_rejections: list[dict[str, Any]] | None = None,
    degraded: list[dict[str, Any]] | None = None,
    physical_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "source_sha256": source_sha256,
        "backend": {"name": backend, "version": backend_version},
        "contract_version": contract_version,
        "page_count": page_count,
        "status": status,
        "outputs": [item.to_dict() for item in sorted(outputs, key=lambda x: x.path)],
        "warnings": warnings or [],
    }
    if elements is not None:
        manifest["elements"] = elements
    if images is not None:
        manifest["images"] = images
    if figures is not None:
        manifest["figures"] = figures
    if figure_rejections is not None:
        manifest["figure_rejections"] = figure_rejections
    if degraded is not None:
        manifest["degraded"] = degraded
    if physical_document is not None:
        manifest["physical_document"] = physical_document
    validate_manifest(manifest)
    return manifest


def validate_manifest(value: dict[str, Any]) -> None:
    required = {
        "manifest_version",
        "source_sha256",
        "backend",
        "contract_version",
        "page_count",
        "status",
        "outputs",
        "warnings",
    }
    optional = {
        "elements",
        "images",
        "figures",
        "figure_rejections",
        "degraded",
        "physical_document",
    }
    if not required.issubset(value) or set(value) - required - optional:
        raise ContractValidationError("manifest 顶层字段不完整或包含未知字段")
    if value["manifest_version"] != MANIFEST_VERSION:
        raise ContractValidationError("manifest_version 不受支持")
    source_hash = value["source_sha256"]
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise ContractValidationError("manifest source_sha256 非法")
    if value["page_count"] <= 0:
        raise ContractValidationError("manifest page_count 必须大于 0")
    if value["status"] not in {"success", "success_with_degradation", "failed"}:
        raise ContractValidationError("manifest status 非法")
    backend = value["backend"]
    if set(backend) != {"name", "version"} or not all(backend.values()):
        raise ContractValidationError("manifest backend 非法")
    paths: set[str] = set()
    for output in value["outputs"]:
        if set(output) != {"path", "role", "size_bytes", "sha256"}:
            raise ContractValidationError("manifest output 字段非法")
        path = Path(output["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ContractValidationError("manifest 不允许绝对路径或路径穿越")
        if output["path"] in paths:
            raise ContractValidationError("manifest output path 重复")
        paths.add(output["path"])
        if output["size_bytes"] < 0 or len(output["sha256"]) != 64:
            raise ContractValidationError("manifest output 大小或哈希非法")
    for field_name in (
        "elements",
        "images",
        "figures",
        "figure_rejections",
        "degraded",
    ):
        if field_name in value and not isinstance(value[field_name], list):
            raise ContractValidationError(f"manifest {field_name} 必须是数组")
    figure_ids: set[str] = set()
    for figure in value.get("figures", []):
        required_figure = {
            "figure_id",
            "page",
            "bbox",
            "member_element_ids",
            "source_object_ids",
            "extraction_mode",
            "asset",
            "native_asset",
            "region_render",
            "caption",
            "evidence_status",
            "degraded_reasons",
            "vector_evidence",
            "markdown_placement",
        }
        if set(figure) != required_figure:
            raise ContractValidationError("manifest figure 字段非法")
        if figure["figure_id"] in figure_ids:
            raise ContractValidationError("manifest figure_id 重复")
        figure_ids.add(figure["figure_id"])
        if figure["page"] <= 0 or not figure["member_element_ids"]:
            raise ContractValidationError("manifest figure 页码或成员非法")
        if figure["extraction_mode"] not in {
            "embedded",
            "grouped",
            "region-rendered",
        }:
            raise ContractValidationError("manifest figure extraction_mode 非法")
        caption = figure["caption"]
        if caption.get("status") not in {"matched", "ambiguous", "none"}:
            raise ContractValidationError("manifest caption status 非法")
        if caption["status"] == "matched" and not caption.get("element_ids"):
            raise ContractValidationError("matched caption 缺少 evidence element IDs")
        if caption["status"] == "matched":
            text = caption.get("text")
            if not isinstance(text, str) or not text:
                raise ContractValidationError("matched caption 缺少规范化文本")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != caption.get(
                "text_sha256"
            ):
                raise ContractValidationError("matched caption 文本哈希不一致")
        asset = figure["asset"]
        path = Path(asset["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ContractValidationError("manifest figure asset 路径非法")
        if len(asset["sha256"]) != 64 or asset["size_bytes"] <= 0:
            raise ContractValidationError("manifest figure asset 哈希或大小非法")
        native_asset = figure["native_asset"]
        if native_asset.get("mode") not in {"embedded", "grouped"}:
            raise ContractValidationError("manifest native_asset mode 非法")
        if (
            len(native_asset.get("sha256", "")) != 64
            or native_asset.get("size_bytes", 0) <= 0
            or native_asset.get("retained_for_provenance") is not True
        ):
            raise ContractValidationError("manifest native_asset 追溯字段非法")
        region = figure["region_render"]
        if region.get("status") not in {"not_requested", "rendered", "rejected"}:
            raise ContractValidationError("manifest region_render status 非法")
        if figure["extraction_mode"] == "region-rendered":
            if region["status"] != "rendered":
                raise ContractValidationError("region-rendered Figure 缺少渲染证据")
            if region.get("source_pdf_sha256") != value["source_sha256"]:
                raise ContractValidationError("region render source hash 不一致")
            if region.get("bbox") != figure["bbox"]:
                raise ContractValidationError("region render bbox 与 Figure bbox 不一致")
            if region.get("page_area_ratio", 1) >= 0.82:
                raise ContractValidationError("region render 近整页区域非法")
    if "physical_document" in value:
        reference = value["physical_document"]
        if set(reference) != {"path", "sha256"}:
            raise ContractValidationError("manifest physical_document 引用非法")
        path = Path(reference["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ContractValidationError("manifest physical_document 路径非法")
        if len(reference["sha256"]) != 64:
            raise ContractValidationError("manifest physical_document 哈希非法")


def canonical_manifest_json(value: dict[str, Any]) -> str:
    validate_manifest(value)
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
