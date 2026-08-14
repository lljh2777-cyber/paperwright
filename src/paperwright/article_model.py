"""Canonical reviewed-article model and deterministic public projections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .exceptions import ContractValidationError
from .reader_contract import (
    BLOCK_FINGERPRINT_VERSION,
    MARKDOWN_ANCHOR_CONTRACT_VERSION,
    READER_CONTRACT_VERSION,
    validate_reader_index,
    visible_block_fingerprint,
)


ARTICLE_MODEL_CONTRACT_VERSION = "paperwright-article-model-v0.1"
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^]]*\]\((?P<path>[^)]+)\)$")
_BLOCK_FIELDS = {
    "id",
    "kind",
    "order",
    "markdown",
    "source_spans",
    "asset_id",
}
_TOP_LEVEL_FIELDS = {
    "contract_version",
    "source_sha256",
    "blocks",
    "assets",
    "relations",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_shape(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != _TOP_LEVEL_FIELDS:
        raise ContractValidationError("article model 顶层字段非法")
    if value["contract_version"] != ARTICLE_MODEL_CONTRACT_VERSION:
        raise ContractValidationError("article model contract_version 不受支持")
    source_sha256 = value["source_sha256"]
    if not isinstance(source_sha256, str) or _HASH_RE.fullmatch(source_sha256) is None:
        raise ContractValidationError("article model source_sha256 非法")

    blocks = value["blocks"]
    if not isinstance(blocks, list) or not blocks:
        raise ContractValidationError("article model blocks 必须是非空数组")
    asset_by_id: dict[object, Mapping[str, Any]] = {}
    if isinstance(value["assets"], list):
        asset_by_id = {
            item.get("id"): item
            for item in value["assets"]
            if isinstance(item, Mapping)
        }
    for expected_order, block in enumerate(blocks, start=1):
        if not isinstance(block, Mapping) or set(block) != _BLOCK_FIELDS:
            raise ContractValidationError("article model block 字段非法")
        if block["order"] != expected_order:
            raise ContractValidationError("article model block order 必须连续")
        markdown = block["markdown"]
        if (
            not isinstance(markdown, str)
            or not markdown
            or "\n" in markdown
            or "\r" in markdown
        ):
            raise ContractValidationError(
                "article model block markdown 必须是非空单行文本"
            )
        if expected_order == 1 and (
            block["kind"] != "title" or not markdown.startswith("# ")
        ):
            raise ContractValidationError(
                "article model 必须以唯一 H1 title block 开始"
            )
        if block["kind"] == "visual_slot":
            match = _MARKDOWN_IMAGE_RE.fullmatch(markdown)
            asset = asset_by_id.get(block["asset_id"])
            if (
                match is None
                or not isinstance(asset, Mapping)
                or match.group("path") != asset.get("path")
            ):
                raise ContractValidationError(
                    "article model visual slot 与 asset path 不一致"
                )
    if not isinstance(value["assets"], list):
        raise ContractValidationError("article model assets 必须是数组")
    if not isinstance(value["relations"], list):
        raise ContractValidationError("article model relations 必须是数组")


def _render_article_markdown(value: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for block in value["blocks"]:
        block_id = block["id"]
        if block["kind"] == "visual_slot":
            marker = (
                f'<!-- pwwd:slot id="{block_id}" '
                f'asset="{block["asset_id"]}" -->'
            )
        else:
            marker = (
                f'<!-- pwwd:block id="{block_id}" '
                f'kind="{block["kind"]}" -->'
            )
        lines.extend((marker, block["markdown"], ""))
    return "\n".join(lines).rstrip() + "\n"


def _reader_index(value: Mapping[str, Any]) -> dict[str, Any]:
    article_text = _render_article_markdown(value)
    blocks = []
    for block in value["blocks"]:
        block_id = block["id"]
        syntax = "pwwd:slot" if block["kind"] == "visual_slot" else "pwwd:block"
        blocks.append(
            {
                "id": block_id,
                "kind": block["kind"],
                "order": block["order"],
                "anchor": {"syntax": syntax, "id": block_id},
                "fingerprint": visible_block_fingerprint(block["markdown"]),
                "source_spans": block["source_spans"],
                "asset_id": block["asset_id"],
            }
        )
    return {
        "contract_version": READER_CONTRACT_VERSION,
        "source_sha256": value["source_sha256"],
        "article": {
            "path": "article.md",
            "sha256": _sha256_text(article_text),
            "anchor_contract": MARKDOWN_ANCHOR_CONTRACT_VERSION,
            "block_fingerprint_version": BLOCK_FINGERPRINT_VERSION,
        },
        "capabilities": {
            "layout_semantics": "reviewed",
            "caption_binding": "reviewed-layout-geometry",
            "body_references": "unavailable",
        },
        "blocks": blocks,
        "assets": list(value["assets"]),
        "relations": list(value["relations"]),
    }


def build_article_model(
    *,
    source_sha256: str,
    blocks: Sequence[Mapping[str, Any]],
    markdown_by_id: Mapping[str, str],
    assets: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the canonical model from reviewed source-backed article blocks."""

    model_blocks = []
    for block in blocks:
        block_id = str(block["id"])
        if block_id not in markdown_by_id:
            raise ContractValidationError(
                f"article model block 缺少 Markdown: {block_id}"
            )
        model_blocks.append(
            {
                "id": block_id,
                "kind": block["kind"],
                "order": block["order"],
                "markdown": markdown_by_id[block_id],
                "source_spans": block["source_spans"],
                "asset_id": block["asset_id"],
            }
        )
    value = {
        "contract_version": ARTICLE_MODEL_CONTRACT_VERSION,
        "source_sha256": source_sha256,
        "blocks": model_blocks,
        "assets": [dict(item) for item in assets],
        "relations": [dict(item) for item in relations],
    }
    validate_article_model(value)
    return value


def validate_article_model(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> None:
    """Validate model structure, identities, graph semantics, and assets."""

    _validate_shape(value)
    reader = _reader_index(value)
    validate_reader_index(
        reader,
        article_text=_render_article_markdown(value),
        root=root,
    )


def render_article_markdown(value: Mapping[str, Any]) -> str:
    """Render public anchored Markdown only after full model validation."""

    validate_article_model(value)
    return _render_article_markdown(value)


def article_model_to_reader(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Project the canonical model into the public reader contract."""

    validate_article_model(value, root=root)
    return _reader_index(value)


def canonical_article_model_json(value: Mapping[str, Any]) -> str:
    validate_article_model(value)
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
