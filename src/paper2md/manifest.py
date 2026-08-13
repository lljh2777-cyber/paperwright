"""Deterministic manifest construction and bootstrap contract validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import ContractValidationError

MANIFEST_VERSION = "paper2md-manifest-v0.4"
AUTO_REGION_MANIFEST_VERSION = "paper2md-manifest-v0.5"
LEGACY_HYBRID_LAYOUT_MANIFEST_VERSION = "paper2md-manifest-v0.6"
PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION = "paper2md-manifest-v0.7"
READER_HYBRID_LAYOUT_MANIFEST_VERSION = "paper2md-manifest-v0.8"
HYBRID_LAYOUT_MANIFEST_VERSION = "paper2md-manifest-v0.9"
TEXT_REVIEWED_MANIFEST_VERSION = "paper2md-manifest-v0.10"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


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
    manifest_version: str = MANIFEST_VERSION,
    region_render_policy: dict[str, Any] | None = None,
    layout_review: dict[str, Any] | None = None,
    reader: dict[str, Any] | None = None,
    article_model: dict[str, Any] | None = None,
    text_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "manifest_version": manifest_version,
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
    if region_render_policy is not None:
        manifest["region_render_policy"] = region_render_policy
    if layout_review is not None:
        manifest["layout_review"] = layout_review
    if reader is not None:
        manifest["reader"] = reader
    if article_model is not None:
        manifest["article_model"] = article_model
    if text_review is not None:
        manifest["text_review"] = text_review
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
        "region_render_policy",
        "layout_review",
        "reader",
        "article_model",
        "text_review",
    }
    if not required.issubset(value) or set(value) - required - optional:
        raise ContractValidationError("manifest 顶层字段不完整或包含未知字段")
    if value["manifest_version"] not in {
        MANIFEST_VERSION,
        AUTO_REGION_MANIFEST_VERSION,
        LEGACY_HYBRID_LAYOUT_MANIFEST_VERSION,
        PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION,
        READER_HYBRID_LAYOUT_MANIFEST_VERSION,
        HYBRID_LAYOUT_MANIFEST_VERSION,
        TEXT_REVIEWED_MANIFEST_VERSION,
    }:
        raise ContractValidationError("manifest_version 不受支持")
    if value["manifest_version"] == MANIFEST_VERSION:
        if (
            "region_render_policy" in value
            or "layout_review" in value
            or "reader" in value
            or "article_model" in value
            or "text_review" in value
        ):
            raise ContractValidationError(
                "manifest v0.4 不允许扩展处理策略"
            )
    elif value["manifest_version"] == AUTO_REGION_MANIFEST_VERSION:
        if (
            "layout_review" in value
            or "reader" in value
            or "article_model" in value
            or "text_review" in value
        ):
            raise ContractValidationError(
                "manifest v0.5 不允许 layout_review/reader"
            )
        policy = value.get("region_render_policy")
        if not isinstance(policy, dict) or set(policy) != {
            "mode",
            "page_indices",
            "max_candidates_per_document",
        }:
            raise ContractValidationError("manifest v0.5 缺少 region_render_policy")
        if policy["mode"] not in {"explicit", "auto"}:
            raise ContractValidationError("manifest region_render_policy mode 非法")
        if (
            not isinstance(policy["page_indices"], list)
            or len(policy["page_indices"]) != len(set(policy["page_indices"]))
            or any(
                not isinstance(item, int) or item < 0
                for item in policy["page_indices"]
            )
        ):
            raise ContractValidationError("manifest region_render_policy 页码非法")
        if (
            not isinstance(policy["max_candidates_per_document"], int)
            or policy["max_candidates_per_document"] <= 0
        ):
            raise ContractValidationError("manifest region_render_policy 上限非法")
    else:
        if "region_render_policy" in value:
            raise ContractValidationError(
                "manifest hybrid 不允许 region_render_policy"
            )
        review = value.get("layout_review")
        required_review = {
            "mode",
            "prompt_version",
            "candidate_generator_version",
            "feature_schema_version",
            "provenance_path",
            "provenance_sha256",
            "ocr_used",
            "pages",
        }
        if value["manifest_version"] in {
            PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION,
            READER_HYBRID_LAYOUT_MANIFEST_VERSION,
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }:
            required_review.add("evidence_level")
        if not isinstance(review, dict) or set(review) != required_review:
            raise ContractValidationError("manifest hybrid 缺少 layout_review")
        if review["mode"] != "hybrid-reviewed" or review["ocr_used"] is not False:
            raise ContractValidationError("manifest layout_review 模式非法")
        if value["manifest_version"] in {
            PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION,
            READER_HYBRID_LAYOUT_MANIFEST_VERSION,
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }:
            if review["evidence_level"] not in {"minimal", "standard", "full"}:
                raise ContractValidationError("manifest evidence_level 非法")
            if review["evidence_level"] == "minimal":
                if (
                    review["provenance_path"] is not None
                    or review["provenance_sha256"] is not None
                ):
                    raise ContractValidationError(
                        "minimal manifest 不应引用 provenance"
                    )
            elif (
                not isinstance(review["provenance_path"], str)
                or not isinstance(review["provenance_sha256"], str)
            ):
                raise ContractValidationError(
                    "standard/full manifest 缺少 provenance"
                )
        if review["provenance_path"] is not None:
            provenance_path = Path(review["provenance_path"])
            if provenance_path.is_absolute() or ".." in provenance_path.parts:
                raise ContractValidationError(
                    "manifest layout_review provenance 路径非法"
                )
        if (
            review["provenance_sha256"] is not None
            and len(review["provenance_sha256"]) != 64
        ):
            raise ContractValidationError(
                "manifest layout_review provenance 哈希非法"
            )
        if not isinstance(review["pages"], list):
            raise ContractValidationError("manifest layout_review pages 必须是数组")
        page_indices: set[int] = set()
        for page in review["pages"]:
            if set(page) != {
                "page_index",
                "task_sha256",
                "final_layout_sha256",
                "reviewer",
                "region_count",
            }:
                raise ContractValidationError(
                    "manifest layout_review page 字段非法"
                )
            if (
                not isinstance(page["page_index"], int)
                or page["page_index"] < 0
                or page["page_index"] in page_indices
                or len(page["task_sha256"]) != 64
                or len(page["final_layout_sha256"]) != 64
                or not page["reviewer"]
                or not isinstance(page["region_count"], int)
                or page["region_count"] < 0
            ):
                raise ContractValidationError(
                    "manifest layout_review page 内容非法"
                )
            page_indices.add(page["page_index"])
        if value["manifest_version"] in {
            READER_HYBRID_LAYOUT_MANIFEST_VERSION,
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }:
            reader = value.get("reader")
            if not isinstance(reader, dict) or set(reader) != {
                "contract_version",
                "path",
                "sha256",
                "article_path",
                "article_sha256",
                "anchor_contract",
            }:
                raise ContractValidationError("manifest hybrid 缺少 reader")
            if (
                reader["contract_version"] != "paper2md-reader-v0.1"
                or reader["anchor_contract"]
                != "paper2md-markdown-anchor-v0.1"
            ):
                raise ContractValidationError("manifest reader 契约非法")
            if (
                reader["path"] != "_paper2md/reader.json"
                or reader["article_path"] != "article.md"
            ):
                raise ContractValidationError("manifest reader 路径非法")
            if (
                not isinstance(reader["sha256"], str)
                or not isinstance(reader["article_sha256"], str)
                or len(reader["sha256"]) != 64
                or len(reader["article_sha256"]) != 64
            ):
                raise ContractValidationError("manifest reader 哈希非法")
        elif "reader" in value:
            raise ContractValidationError(
                "旧版 manifest 不允许 reader 顶层字段"
            )
        if value["manifest_version"] in {
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }:
            article_model = value.get("article_model")
            if not isinstance(article_model, dict) or set(article_model) != {
                "contract_version",
                "path",
                "sha256",
            }:
                raise ContractValidationError("manifest v0.9+ 缺少 article_model")
            if (
                article_model["contract_version"]
                != "paper2md-article-model-v0.1"
                or article_model["path"] != "_paper2md/article-model.json"
                or not isinstance(article_model["sha256"], str)
                or len(article_model["sha256"]) != 64
            ):
                raise ContractValidationError("manifest article_model 契约非法")
        elif "article_model" in value:
            raise ContractValidationError(
                "旧版 manifest 不允许 article_model 顶层字段"
            )
        if value["manifest_version"] == TEXT_REVIEWED_MANIFEST_VERSION:
            text_review = value.get("text_review")
            required_text_review = {
                "task_contract_version",
                "review_contract_version",
                "task_path",
                "task_sha256",
                "review_path",
                "review_sha256",
                "source_article_model_sha256",
                "parent_manifest_sha256",
                "reviewer",
                "operation_count",
                "validation_path",
                "validation_sha256",
            }
            if (
                not isinstance(text_review, dict)
                or set(text_review) != required_text_review
            ):
                raise ContractValidationError("manifest v0.10 缺少 text_review")
            if (
                text_review["task_contract_version"]
                not in ("paper2md-text-task-v0.1", "paper2md-text-task-v0.2")
                or text_review["review_contract_version"]
                not in ("paper2md-text-review-v0.1", "paper2md-text-review-v0.2")
                or text_review["task_path"]
                != "_paper2md/06-text-review/text-task.json"
                or text_review["review_path"]
                != "_paper2md/06-text-review/text-review.json"
                or text_review["validation_path"]
                != "_paper2md/06-text-review/validation-report.json"
                or any(
                    not isinstance(text_review[field], str)
                    or _SHA256_RE.fullmatch(text_review[field]) is None
                    for field in (
                        "task_sha256",
                        "review_sha256",
                        "source_article_model_sha256",
                        "parent_manifest_sha256",
                        "validation_sha256",
                    )
                )
                or not isinstance(text_review["reviewer"], str)
                or not text_review["reviewer"]
                or len(text_review["reviewer"]) > 200
                or isinstance(text_review["operation_count"], bool)
                or not isinstance(text_review["operation_count"], int)
                or text_review["operation_count"] < 0
            ):
                raise ContractValidationError("manifest text_review 契约非法")
        elif "text_review" in value:
            raise ContractValidationError(
                "旧版 manifest 不允许 text_review 顶层字段"
            )
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
    if "reader" in value:
        by_path = {item["path"]: item for item in value["outputs"]}
        reader = value["reader"]
        reader_output = by_path.get(reader["path"])
        article_output = by_path.get(reader["article_path"])
        if (
            reader_output is None
            or reader_output["role"] != "reader_index"
            or reader_output["sha256"] != reader["sha256"]
            or article_output is None
            or article_output["role"] != "markdown"
            or article_output["sha256"] != reader["article_sha256"]
        ):
            raise ContractValidationError(
                "manifest reader 与 outputs 清单不一致"
            )
    if "article_model" in value:
        by_path = {item["path"]: item for item in value["outputs"]}
        article_model = value["article_model"]
        model_output = by_path.get(article_model["path"])
        if (
            model_output is None
            or model_output["role"] != "article_model"
            or model_output["sha256"] != article_model["sha256"]
        ):
            raise ContractValidationError(
                "manifest article_model 与 outputs 清单不一致"
            )
    if "text_review" in value:
        by_path = {item["path"]: item for item in value["outputs"]}
        text_review = value["text_review"]
        for path_field, hash_field, role in (
            ("task_path", "task_sha256", "text_task"),
            ("review_path", "review_sha256", "text_review"),
            (
                "validation_path",
                "validation_sha256",
                "text_validation_report",
            ),
        ):
            output = by_path.get(text_review[path_field])
            if (
                output is None
                or output["role"] != role
                or output["sha256"] != text_review[hash_field]
            ):
                raise ContractValidationError(
                    "manifest text_review 与 outputs 清单不一致"
                )
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
