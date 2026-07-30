"""Deterministic manifest construction and bootstrap contract validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import ContractValidationError

MANIFEST_VERSION = "paper2md-manifest-v0.2"


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
    optional = {"elements", "images", "degraded", "physical_document"}
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
    for field_name in ("elements", "images", "degraded"):
        if field_name in value and not isinstance(value[field_name], list):
            raise ContractValidationError(f"manifest {field_name} 必须是数组")
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
