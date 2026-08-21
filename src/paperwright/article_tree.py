"""Canonical final article tree and its lossless ArticleModel projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .article_model import build_article_model, validate_article_model
from .exceptions import ContractValidationError


ARTICLE_TREE_CONTRACT_VERSION = "paperwright-article-tree-v0.2"
ARTICLE_TREE_COMPILER_VERSION = "paperwright-final-article-tree-compiler-v0.1"
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_STRUCTURE_INPUT_KINDS = {
    "legacy_article_model",
    "source_element_tree",
    "text_review",
    "reviewed_layouts",
    "reviewed_projection",
}
_TOP_LEVEL_FIELDS = {
    "contract_version",
    "compiler_version",
    "source_sha256",
    "physical_document_sha256",
    "structure_input",
    "root_node_id",
    "nodes",
    "assets",
    "relations",
    "summary",
}
_NODE_FIELDS = {
    "node_id",
    "parent_id",
    "node_kind",
    "role",
    "order",
    "markdown",
    "source_spans",
    "asset_id",
    "content_origin",
}


def _canonical_payload(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def reviewed_projection_sha256(
    *,
    blocks: Sequence[Mapping[str, Any]],
    markdown_by_id: Mapping[str, str],
    assets: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> str:
    """Identify the complete reviewed projection consumed by the tree compiler."""

    payload = {
        "blocks": [dict(item) for item in blocks],
        "markdown_by_id": dict(markdown_by_id),
        "assets": [dict(item) for item in assets],
        "relations": [dict(item) for item in relations],
    }
    return hashlib.sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()


def build_final_article_tree(
    *,
    source_sha256: str,
    physical_document_sha256: str | None,
    structure_input_kind: str,
    structure_input_sha256: str,
    blocks: Sequence[Mapping[str, Any]],
    markdown_by_id: Mapping[str, str],
    assets: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compile reviewed blocks into the sole canonical article structure."""

    nodes: list[dict[str, Any]] = [
        {
            "node_id": "article",
            "parent_id": None,
            "node_kind": "article",
            "role": "article",
            "order": 0,
            "markdown": None,
            "source_spans": [],
            "asset_id": None,
            "content_origin": "container",
        }
    ]
    for block in blocks:
        block_id = str(block["id"])
        markdown = markdown_by_id.get(block_id)
        if markdown is None:
            raise ContractValidationError(
                f"final ArticleTree block 缺少 Markdown: {block_id}"
            )
        nodes.append(
            {
                "node_id": block_id,
                "parent_id": "article",
                "node_kind": "block",
                "role": block["kind"],
                "order": block["order"],
                "markdown": markdown,
                "source_spans": [dict(item) for item in block["source_spans"]],
                "asset_id": block["asset_id"],
                "content_origin": "source_projection",
            }
        )
    value = {
        "contract_version": ARTICLE_TREE_CONTRACT_VERSION,
        "compiler_version": ARTICLE_TREE_COMPILER_VERSION,
        "source_sha256": source_sha256,
        "physical_document_sha256": physical_document_sha256,
        "structure_input": {
            "kind": structure_input_kind,
            "sha256": structure_input_sha256,
        },
        "root_node_id": "article",
        "nodes": nodes,
        "assets": [dict(item) for item in assets],
        "relations": [dict(item) for item in relations],
        "summary": {
            "block_count": len(nodes) - 1,
            "asset_count": len(assets),
            "relation_count": len(relations),
            "generated_text_count": 0,
        },
    }
    validate_final_article_tree(value)
    return value


def _project_article_model(value: Mapping[str, Any]) -> dict[str, Any]:
    block_nodes = [
        item for item in value["nodes"] if item["node_kind"] == "block"
    ]
    return build_article_model(
        source_sha256=str(value["source_sha256"]),
        blocks=[
            {
                "id": item["node_id"],
                "kind": item["role"],
                "order": item["order"],
                "source_spans": item["source_spans"],
                "asset_id": item["asset_id"],
            }
            for item in block_nodes
        ],
        markdown_by_id={
            str(item["node_id"]): str(item["markdown"])
            for item in block_nodes
        },
        assets=value["assets"],
        relations=value["relations"],
    )


def validate_final_article_tree(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
    expected_source_sha256: str | None = None,
    expected_physical_document_sha256: str | None = None,
) -> None:
    """Validate the final tree, its input binding, and ArticleModel projection."""

    if not isinstance(value, Mapping) or set(value) != _TOP_LEVEL_FIELDS:
        raise ContractValidationError("final ArticleTree 顶层字段非法")
    if (
        value["contract_version"] != ARTICLE_TREE_CONTRACT_VERSION
        or value["compiler_version"] != ARTICLE_TREE_COMPILER_VERSION
        or value["root_node_id"] != "article"
    ):
        raise ContractValidationError("final ArticleTree 版本或根节点非法")
    source_sha256 = value["source_sha256"]
    if (
        not isinstance(source_sha256, str)
        or _HASH_RE.fullmatch(source_sha256) is None
    ):
        raise ContractValidationError("final ArticleTree source_sha256 非法")
    physical_sha256 = value["physical_document_sha256"]
    if physical_sha256 is not None and (
        not isinstance(physical_sha256, str)
        or _HASH_RE.fullmatch(physical_sha256) is None
    ):
        raise ContractValidationError(
            "final ArticleTree physical_document_sha256 非法"
        )
    if (
        expected_source_sha256 is not None
        and value["source_sha256"] != expected_source_sha256
    ) or (
        expected_physical_document_sha256 is not None
        and physical_sha256 != expected_physical_document_sha256
    ):
        raise ContractValidationError("final ArticleTree 输入身份不匹配")

    structure_input = value["structure_input"]
    if (
        not isinstance(structure_input, Mapping)
        or set(structure_input) != {"kind", "sha256"}
        or structure_input["kind"] not in _STRUCTURE_INPUT_KINDS
        or not isinstance(structure_input["sha256"], str)
        or _HASH_RE.fullmatch(structure_input["sha256"]) is None
    ):
        raise ContractValidationError("final ArticleTree structure_input 非法")

    nodes = value["nodes"]
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ContractValidationError("final ArticleTree nodes 必须包含文章和 block")
    if any(
        not isinstance(item, Mapping) or set(item) != _NODE_FIELDS
        for item in nodes
    ):
        raise ContractValidationError("final ArticleTree node 字段非法")
    root_node = nodes[0]
    if root_node != {
        "node_id": "article",
        "parent_id": None,
        "node_kind": "article",
        "role": "article",
        "order": 0,
        "markdown": None,
        "source_spans": [],
        "asset_id": None,
        "content_origin": "container",
    }:
        raise ContractValidationError("final ArticleTree article 根节点非法")
    block_nodes = nodes[1:]
    node_ids = [item["node_id"] for item in nodes]
    if any(not isinstance(item, str) or not item for item in node_ids) or len(
        node_ids
    ) != len(set(node_ids)):
        raise ContractValidationError("final ArticleTree node_id 非法或重复")
    for expected_order, node in enumerate(block_nodes, start=1):
        if (
            node["node_kind"] != "block"
            or node["parent_id"] != "article"
            or node["order"] != expected_order
            or node["content_origin"] != "source_projection"
            or not isinstance(node["markdown"], str)
            or not node["markdown"]
            or not isinstance(node["source_spans"], list)
        ):
            raise ContractValidationError("final ArticleTree block 节点非法")
    if not isinstance(value["assets"], list) or not isinstance(
        value["relations"], list
    ):
        raise ContractValidationError("final ArticleTree assets/relations 必须是数组")
    summary = value["summary"]
    expected_summary = {
        "block_count": len(block_nodes),
        "asset_count": len(value["assets"]),
        "relation_count": len(value["relations"]),
        "generated_text_count": 0,
    }
    if not isinstance(summary, Mapping) or dict(summary) != expected_summary:
        raise ContractValidationError("final ArticleTree summary 不一致")

    model = _project_article_model(value)
    validate_article_model(model, root=root)


def article_tree_to_article_model(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Project ArticleModel only from a fully validated final ArticleTree."""

    validate_final_article_tree(value, root=root)
    return _project_article_model(value)


def canonical_final_article_tree_json(value: Mapping[str, Any]) -> str:
    validate_final_article_tree(value)
    return _canonical_payload(value) + "\n"


__all__ = [
    "ARTICLE_TREE_COMPILER_VERSION",
    "ARTICLE_TREE_CONTRACT_VERSION",
    "article_tree_to_article_model",
    "build_final_article_tree",
    "canonical_final_article_tree_json",
    "reviewed_projection_sha256",
    "validate_final_article_tree",
]
