"""Strict validation for the Paper2MD reader interoperability contract."""

from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping
import unicodedata

from .exceptions import ContractValidationError
from .manifest import sha256_file


READER_CONTRACT_VERSION = "paper2md-reader-v0.1"
MARKDOWN_ANCHOR_CONTRACT_VERSION = "paper2md-markdown-anchor-v0.1"
BLOCK_FINGERPRINT_VERSION = "paper2md-visible-block-fingerprint-v0.1"

VALID_BLOCK_KINDS = {
    "title",
    "heading",
    "body",
    "caption",
    "footnote",
    "visual_slot",
    "unknown",
}
VALID_ASSET_KINDS = {"figure", "table", "equation", "unknown"}
VALID_RELATIONS = {"places", "caption-of"}

_PUBLIC_ANCHOR_RE = re.compile(
    r'^<!-- p2md:(?P<syntax>block|slot) id="(?P<id>[a-z]+_[0-9a-f]{24})"'
    r'(?: kind="(?P<kind>[a-z_]+)")?'
    r'(?: asset="(?P<asset_id>ast_[0-9a-f]{24})")? -->$'
)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_payload(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_reader_id(prefix: str, source_sha256: str, payload: object) -> str:
    digest = hashlib.sha256(
        (
            f"paper2md-reader-id-v0.1\0{source_sha256}\0"
            f"{prefix}\0{canonical_payload(payload)}"
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:24]}"


def normalized_visible_text(markdown: str) -> str:
    value = markdown.strip()
    value = re.sub(r"^#{1,6}\s+", "", value)
    value = value.removeprefix("&emsp;")
    value = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`~]", "", value)
    value = html.unescape(value)
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _simhash64(value: str) -> str:
    tokens = _TOKEN_RE.findall(value.casefold())
    if not tokens:
        return "0000000000000000"
    weights = [0] * 64
    for token in tokens:
        number = int.from_bytes(
            hashlib.sha256(token.encode("utf-8")).digest()[:8], "big"
        )
        for index in range(64):
            weights[index] += 1 if number & (1 << index) else -1
    result = sum(
        1 << index for index, weight in enumerate(weights) if weight >= 0
    )
    return f"{result:016x}"


def visible_block_fingerprint(markdown: str) -> dict[str, object]:
    visible = normalized_visible_text(markdown)
    return {
        "visible_text_sha256": _sha256_text(visible),
        "simhash64": _simhash64(visible),
        "text_length": len(visible),
    }


def _safe_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"reader {field} 必须是非空路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ContractValidationError(f"reader {field} 路径非法")
    return value


def _validate_source_spans(value: object) -> None:
    if not isinstance(value, list):
        raise ContractValidationError("reader source_spans 必须是数组")
    for span in value:
        if not isinstance(span, dict) or set(span) != {
            "page_index",
            "bbox",
            "region_id",
            "paragraph_index",
            "elements_sha256",
        }:
            raise ContractValidationError("reader source span 字段非法")
        if (
            isinstance(span["page_index"], bool)
            or not isinstance(span["page_index"], int)
            or span["page_index"] < 0
        ):
            raise ContractValidationError("reader source span page_index 非法")
        if span["region_id"] is not None and not isinstance(
            span["region_id"], str
        ):
            raise ContractValidationError("reader source span region_id 非法")
        if span["paragraph_index"] is not None and (
            isinstance(span["paragraph_index"], bool)
            or not isinstance(span["paragraph_index"], int)
            or span["paragraph_index"] < 0
        ):
            raise ContractValidationError(
                "reader source span paragraph_index 非法"
            )
        if not isinstance(span["elements_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", span["elements_sha256"]
        ):
            raise ContractValidationError("reader source span hash 非法")
        bbox = span["bbox"]
        if not isinstance(bbox, dict) or set(bbox) != {
            "x",
            "y",
            "width",
            "height",
        }:
            raise ContractValidationError("reader source span bbox 字段非法")
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in bbox.values()
        ) or any(not math.isfinite(float(item)) for item in bbox.values()):
            raise ContractValidationError("reader source span bbox 类型非法")
        if (
            bbox["x"] < 0
            or bbox["y"] < 0
            or bbox["width"] <= 0
            or bbox["height"] <= 0
            or bbox["x"] + bbox["width"] > 1.00000001
            or bbox["y"] + bbox["height"] > 1.00000001
        ):
            raise ContractValidationError("reader source span bbox 越界")


def _validate_article(value: Mapping[str, Any]) -> Mapping[str, Any]:
    article = value["article"]
    if not isinstance(article, dict) or set(article) != {
        "path",
        "sha256",
        "anchor_contract",
        "block_fingerprint_version",
    }:
        raise ContractValidationError("reader article 字段非法")
    if _safe_relative_path(article["path"], "article.path") != "article.md":
        raise ContractValidationError("reader article.path 必须是 article.md")
    if article["anchor_contract"] != MARKDOWN_ANCHOR_CONTRACT_VERSION:
        raise ContractValidationError("reader anchor contract 不受支持")
    if article["block_fingerprint_version"] != BLOCK_FINGERPRINT_VERSION:
        raise ContractValidationError("reader fingerprint contract 不受支持")
    if not isinstance(article["sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", article["sha256"]
    ):
        raise ContractValidationError("reader article hash 非法")
    return article


def _validate_capabilities(value: Mapping[str, Any]) -> None:
    capabilities = value["capabilities"]
    if not isinstance(capabilities, dict) or set(capabilities) != {
        "layout_semantics",
        "caption_binding",
        "body_references",
    }:
        raise ContractValidationError("reader capabilities 字段非法")
    if capabilities != {
        "layout_semantics": "reviewed",
        "caption_binding": "reviewed-layout-geometry",
        "body_references": "unavailable",
    }:
        raise ContractValidationError("reader capabilities 内容非法")


def _validate_blocks(
    blocks: object,
    *,
    source_sha256: str,
) -> tuple[set[str], dict[str, Mapping[str, Any]]]:
    if not isinstance(blocks, list):
        raise ContractValidationError("reader blocks 必须是数组")
    block_ids: set[str] = set()
    block_by_id: dict[str, Mapping[str, Any]] = {}
    orders: list[int] = []
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {
            "id",
            "kind",
            "order",
            "anchor",
            "fingerprint",
            "source_spans",
            "asset_id",
        }:
            raise ContractValidationError("reader block 字段非法")
        block_id = block["id"]
        if (
            not isinstance(block_id, str)
            or not re.fullmatch(r"(?:blk|slot)_[0-9a-f]{24}", block_id)
            or block_id in block_ids
        ):
            raise ContractValidationError("reader block id 非法或重复")
        block_ids.add(block_id)
        block_by_id[block_id] = block
        if (
            not isinstance(block["kind"], str)
            or block["kind"] not in VALID_BLOCK_KINDS
        ):
            raise ContractValidationError("reader block kind 非法")
        if (
            block["kind"] == "visual_slot"
            and not block_id.startswith("slot_")
        ) or (
            block["kind"] != "visual_slot"
            and not block_id.startswith("blk_")
        ):
            raise ContractValidationError("reader block id 与 kind 不一致")
        if (
            isinstance(block["order"], bool)
            or not isinstance(block["order"], int)
            or block["order"] < 1
        ):
            raise ContractValidationError("reader block order 非法")
        orders.append(block["order"])
        anchor = block["anchor"]
        expected_syntax = (
            "p2md:slot"
            if block["kind"] == "visual_slot"
            else "p2md:block"
        )
        if anchor != {"syntax": expected_syntax, "id": block_id}:
            raise ContractValidationError("reader block anchor 非法")
        asset_id = block["asset_id"]
        if block["kind"] == "visual_slot":
            if not isinstance(asset_id, str) or not re.fullmatch(
                r"ast_[0-9a-f]{24}", asset_id
            ):
                raise ContractValidationError("reader visual slot asset_id 非法")
        elif asset_id is not None:
            raise ContractValidationError("reader 非视觉 block 不允许 asset_id")
        fingerprint = block["fingerprint"]
        if not isinstance(fingerprint, dict) or set(fingerprint) != {
            "visible_text_sha256",
            "simhash64",
            "text_length",
        }:
            raise ContractValidationError("reader block fingerprint 非法")
        if (
            not isinstance(fingerprint["visible_text_sha256"], str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", fingerprint["visible_text_sha256"]
            )
            or not isinstance(fingerprint["simhash64"], str)
            or not re.fullmatch(r"[0-9a-f]{16}", fingerprint["simhash64"])
            or not isinstance(fingerprint["text_length"], int)
            or isinstance(fingerprint["text_length"], bool)
            or fingerprint["text_length"] < 0
        ):
            raise ContractValidationError("reader block fingerprint 内容非法")
        _validate_source_spans(block["source_spans"])
        if block["kind"] != "title" and not block["source_spans"]:
            raise ContractValidationError("reader 非标题 block 缺少 source span")
        if block["kind"] == "visual_slot":
            prefix = "slot"
            identity = {
                "asset_id": block["asset_id"],
                "source_spans": block["source_spans"],
            }
        else:
            prefix = "blk"
            identity = {
                "kind": block["kind"],
                "source_spans": block["source_spans"],
            }
            if block["kind"] == "title" and not block["source_spans"]:
                identity["fallback_visible_text_sha256"] = fingerprint[
                    "visible_text_sha256"
                ]
        if block_id != stable_reader_id(prefix, source_sha256, identity):
            raise ContractValidationError("reader block id 与稳定身份不一致")
    if orders != list(range(1, len(orders) + 1)):
        raise ContractValidationError("reader block order 必须连续")
    if not blocks or blocks[0]["kind"] != "title" or sum(
        item["kind"] == "title" for item in blocks
    ) != 1:
        raise ContractValidationError("reader blocks 必须以唯一 title 开始")
    return block_ids, block_by_id


def _validate_assets(
    assets: object,
    *,
    block_ids: set[str],
    block_by_id: Mapping[str, Mapping[str, Any]],
    source_sha256: str,
    root: Path | None,
) -> tuple[set[str], dict[str, Mapping[str, Any]]]:
    if not isinstance(assets, list):
        raise ContractValidationError("reader assets 必须是数组")
    asset_ids: set[str] = set()
    asset_by_id: dict[str, Mapping[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {
            "id",
            "kind",
            "path",
            "sha256",
            "size_bytes",
            "width_px",
            "height_px",
            "display_label",
            "caption_block_id",
            "placement_block_id",
            "source_spans",
        }:
            raise ContractValidationError("reader asset 字段非法")
        asset_id = asset["id"]
        if (
            not isinstance(asset_id, str)
            or not re.fullmatch(r"ast_[0-9a-f]{24}", asset_id)
            or asset_id in asset_ids
        ):
            raise ContractValidationError("reader asset id 非法或重复")
        asset_ids.add(asset_id)
        asset_by_id[asset_id] = asset
        if (
            not isinstance(asset["kind"], str)
            or asset["kind"] not in VALID_ASSET_KINDS
        ):
            raise ContractValidationError("reader asset kind 非法")
        asset_path = _safe_relative_path(asset["path"], "asset.path")
        if re.fullmatch(r"images/[^/]+\.png", asset_path) is None:
            raise ContractValidationError("reader asset.path 必须是 images/*.png")
        if (
            not isinstance(asset["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
            or not isinstance(asset["size_bytes"], int)
            or isinstance(asset["size_bytes"], bool)
            or asset["size_bytes"] <= 0
            or not isinstance(asset["width_px"], int)
            or isinstance(asset["width_px"], bool)
            or asset["width_px"] <= 0
            or not isinstance(asset["height_px"], int)
            or isinstance(asset["height_px"], bool)
            or asset["height_px"] <= 0
        ):
            raise ContractValidationError("reader asset 文件信息非法")
        if asset["display_label"] is not None and (
            not isinstance(asset["display_label"], str)
            or not asset["display_label"]
        ):
            raise ContractValidationError("reader display_label 非法")
        placement = asset["placement_block_id"]
        if (
            not isinstance(placement, str)
            or placement not in block_ids
            or block_by_id[placement]["asset_id"] != asset_id
        ):
            raise ContractValidationError("reader asset placement 非法")
        caption = asset["caption_block_id"]
        if caption is not None and (
            not isinstance(caption, str)
            or caption not in block_ids
            or block_by_id[caption]["kind"] != "caption"
        ):
            raise ContractValidationError("reader asset caption 非法")
        _validate_source_spans(asset["source_spans"])
        if not asset["source_spans"]:
            raise ContractValidationError("reader asset 缺少 source span")
        expected_id = stable_reader_id(
            "ast",
            source_sha256,
            {
                "kind": asset["kind"],
                "source_spans": asset["source_spans"],
            },
        )
        if asset_id != expected_id:
            raise ContractValidationError("reader asset id 与稳定身份不一致")
        if root is not None:
            path = root / PurePosixPath(asset_path)
            if (
                not path.is_file()
                or path.stat().st_size != asset["size_bytes"]
                or sha256_file(path) != asset["sha256"]
            ):
                raise ContractValidationError("reader asset 缺失或哈希不匹配")
    return asset_ids, asset_by_id


def _validate_relations(
    relations: object,
    *,
    block_ids: set[str],
    block_by_id: Mapping[str, Mapping[str, Any]],
    asset_ids: set[str],
    asset_by_id: Mapping[str, Mapping[str, Any]],
    source_sha256: str,
) -> None:
    if not isinstance(relations, list):
        raise ContractValidationError("reader relations 必须是数组")
    relation_ids: set[str] = set()
    relation_keys: set[tuple[str, str, str]] = set()
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {
            "id",
            "type",
            "source_id",
            "target_id",
            "label",
        }:
            raise ContractValidationError("reader relation 字段非法")
        if (
            not isinstance(relation["id"], str)
            or not re.fullmatch(r"rel_[0-9a-f]{24}", relation["id"])
            or relation["id"] in relation_ids
        ):
            raise ContractValidationError("reader relation id 非法或重复")
        relation_ids.add(relation["id"])
        if (
            not isinstance(relation["type"], str)
            or relation["type"] not in VALID_RELATIONS
        ):
            raise ContractValidationError("reader relation type 非法")
        if not isinstance(relation["source_id"], str) or not isinstance(
            relation["target_id"], str
        ):
            raise ContractValidationError("reader relation 端点非法")
        key = (
            str(relation["type"]),
            str(relation["source_id"]),
            str(relation["target_id"]),
        )
        if key in relation_keys:
            raise ContractValidationError("reader relation 重复")
        relation_keys.add(key)
        if relation["source_id"] not in block_ids:
            raise ContractValidationError("reader relation source 不存在")
        if relation["target_id"] not in asset_ids:
            raise ContractValidationError("reader relation target 不存在")
        if relation["label"] is not None and not isinstance(
            relation["label"], str
        ):
            raise ContractValidationError("reader relation label 非法")
        source = block_by_id[str(relation["source_id"])]
        target = asset_by_id[str(relation["target_id"])]
        if relation["type"] == "places" and (
            source["kind"] != "visual_slot"
            or source["asset_id"] != relation["target_id"]
            or target["placement_block_id"] != relation["source_id"]
            or relation["label"] is not None
        ):
            raise ContractValidationError("reader places 关系内容非法")
        if relation["type"] == "caption-of" and (
            source["kind"] != "caption"
            or target["caption_block_id"] != relation["source_id"]
            or relation["label"] != target["display_label"]
        ):
            raise ContractValidationError("reader caption-of 关系内容非法")
        expected_id = stable_reader_id(
            "rel",
            source_sha256,
            {
                "type": relation["type"],
                "source_id": relation["source_id"],
                "target_id": relation["target_id"],
                "label": relation["label"],
            },
        )
        if relation["id"] != expected_id:
            raise ContractValidationError("reader relation id 与稳定身份不一致")
    for asset_id, asset in asset_by_id.items():
        if (
            "places",
            str(asset["placement_block_id"]),
            asset_id,
        ) not in relation_keys:
            raise ContractValidationError("reader asset 缺少 places 关系")
        caption = asset["caption_block_id"]
        if caption is not None and (
            "caption-of",
            str(caption),
            asset_id,
        ) not in relation_keys:
            raise ContractValidationError("reader asset 缺少 caption-of 关系")


def _validate_markdown_anchors(
    article_text: str,
    *,
    article: Mapping[str, Any],
    block_ids: set[str],
    block_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    if _sha256_text(article_text) != article["sha256"]:
        raise ContractValidationError("reader article hash 不匹配")
    anchors: dict[str, tuple[str, str | None, str | None]] = {}
    lines = article_text.splitlines()
    anchored_markdown: dict[str, str] = {}
    content_indexes: set[int] = set()
    for index, line in enumerate(lines):
        match = _PUBLIC_ANCHOR_RE.match(line)
        if match is None:
            continue
        anchor_id = match.group("id")
        if anchor_id in anchors:
            raise ContractValidationError("reader Markdown anchor 重复")
        anchors[anchor_id] = (
            match.group("syntax"),
            match.group("kind"),
            match.group("asset_id"),
        )
        if index + 1 >= len(lines) or not lines[index + 1]:
            raise ContractValidationError("reader Markdown anchor 缺少内容块")
        anchored_markdown[anchor_id] = lines[index + 1]
        content_indexes.add(index + 1)
    if set(anchors) != block_ids:
        raise ContractValidationError("reader Markdown anchor 集合不一致")
    for block_id, block in block_by_id.items():
        syntax, kind, asset_id = anchors[block_id]
        expected_syntax = "slot" if block["kind"] == "visual_slot" else "block"
        expected_kind = None if expected_syntax == "slot" else block["kind"]
        if (
            syntax != expected_syntax
            or kind != expected_kind
            or asset_id != block["asset_id"]
        ):
            raise ContractValidationError("reader Markdown anchor 内容不一致")
        if visible_block_fingerprint(anchored_markdown[block_id]) != block[
            "fingerprint"
        ]:
            raise ContractValidationError("reader Markdown block 指纹不一致")
    for index, line in enumerate(lines):
        if (
            line
            and index not in content_indexes
            and _PUBLIC_ANCHOR_RE.match(line) is None
            and not (line.lstrip().startswith("<!--") and line.endswith("-->"))
        ):
            raise ContractValidationError("reader Markdown 存在未锚定内容")


def validate_reader_index(
    value: Mapping[str, Any],
    *,
    article_text: str | None = None,
    root: Path | None = None,
) -> None:
    """Validate the reader contract, public anchors, relations, and assets."""

    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "source_sha256",
        "article",
        "capabilities",
        "blocks",
        "assets",
        "relations",
    }:
        raise ContractValidationError("reader 顶层字段非法")
    if value["contract_version"] != READER_CONTRACT_VERSION:
        raise ContractValidationError("reader contract_version 不受支持")
    source_sha256 = value["source_sha256"]
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise ContractValidationError("reader source_sha256 非法")
    article = _validate_article(value)
    _validate_capabilities(value)
    block_ids, block_by_id = _validate_blocks(
        value["blocks"], source_sha256=source_sha256
    )
    asset_ids, asset_by_id = _validate_assets(
        value["assets"],
        block_ids=block_ids,
        block_by_id=block_by_id,
        source_sha256=source_sha256,
        root=root,
    )
    for block in block_by_id.values():
        if block["kind"] == "visual_slot" and block["asset_id"] not in asset_ids:
            raise ContractValidationError("reader visual slot 缺少对应 asset")
    caption_ids = [
        item["caption_block_id"]
        for item in asset_by_id.values()
        if item["caption_block_id"] is not None
    ]
    if len(caption_ids) != len(set(caption_ids)):
        raise ContractValidationError("reader caption block 被多个 asset 复用")
    _validate_relations(
        value["relations"],
        block_ids=block_ids,
        block_by_id=block_by_id,
        asset_ids=asset_ids,
        asset_by_id=asset_by_id,
        source_sha256=source_sha256,
    )
    if article_text is not None:
        _validate_markdown_anchors(
            article_text,
            article=article,
            block_ids=block_ids,
            block_by_id=block_by_id,
        )


def canonical_reader_json(value: Mapping[str, Any]) -> str:
    validate_reader_index(value)
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
