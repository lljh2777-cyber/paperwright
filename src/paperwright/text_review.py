"""Deterministic task and review contracts for source-preserving text cleanup."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from .article_model import (
    ARTICLE_MODEL_CONTRACT_VERSION,
    canonical_article_model_json,
    validate_article_model,
)
from .exceptions import ContractValidationError
from .reader_contract import VALID_BLOCK_KINDS, normalized_visible_text


TEXT_TASK_CONTRACT_VERSION = "paperwright-text-task-v0.2"
TEXT_REVIEW_CONTRACT_VERSION = "paperwright-text-review-v0.2"
TEXT_TASK_CONTRACT_VERSION_V1 = "paperwright-text-task-v0.1"
TEXT_REVIEW_CONTRACT_VERSION_V1 = "paperwright-text-review-v0.1"
TEXT_EQUIVALENCE_VERSION = "paperwright-text-equivalence-v0.1"

_JOIN_OPERATIONS = ("join-blocks",)
_ALLOWED_OPERATIONS_V1 = ("replace-markdown",)
_ALLOWED_OPERATIONS_V2 = ("replace-markdown", "join-blocks")

_HASH_RE = re.compile(r"[0-9a-f]{64}")
_BLOCK_ID_RE = re.compile(r"(?:blk|slot)_[0-9a-f]{24}")
_DEHYPHENATION_RE = re.compile(r"(?<=\w)-\s+(?=\w)", re.UNICODE)
_HEADING_PREFIX_RE = re.compile(r"^(#{1,6})\s+")
_EDITABLE_KINDS = frozenset(VALID_BLOCK_KINDS - {"visual_slot"})
_CHANGE_MODES = ("format-only", "dehyphenation")
_IMMUTABLE_FIELDS = (
    "id",
    "kind",
    "order",
    "source_spans",
    "asset_id",
    "assets",
    "relations",
)
_TASK_FIELDS = {
    "contract_version",
    "source_sha256",
    "article_model",
    "policy",
    "blocks",
}
_TASK_MODEL_FIELDS = {"contract_version", "sha256"}
_TASK_POLICY_FIELDS = {
    "text_source",
    "allowed_operations",
    "allowed_change_modes",
    "immutable_fields",
    "text_equivalence_version",
}
_TASK_BLOCK_FIELDS = {
    "id",
    "kind",
    "order",
    "page",
    "markdown",
    "markdown_sha256",
    "visible_text_sha256",
    "editable",
    "in_relations",
}
_REVIEW_FIELDS = {
    "contract_version",
    "task_sha256",
    "source_sha256",
    "article_model_sha256",
    "reviewer",
    "operations",
}
_REPLACE_OPERATION_FIELDS = {
    "op",
    "block_id",
    "expected_markdown_sha256",
    "change_mode",
    "markdown",
    "reason",
}
_JOIN_OPERATION_FIELDS = {
    "op",
    "target_block_ids",
    "reason",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _valid_single_line(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\n" not in value
        and "\r" not in value
    )


def _visible_sha256(markdown: str) -> str:
    return _sha256_text(normalized_visible_text(markdown))


def _dehyphenated_visible(markdown: str) -> str:
    return _DEHYPHENATION_RE.sub("", normalized_visible_text(markdown))


def _block_page(block: Mapping[str, Any]) -> int:
    """First source page index for a block; -1 when no source span is present."""
    for span in block.get("source_spans") or []:
        page = span.get("page_index")
        if isinstance(page, int):
            return page
    return -1


def _task_blocks(article_model: Mapping[str, Any]) -> list[dict[str, Any]]:
    relation_ids: set[str] = set()
    for relation in article_model.get("relations") or []:
        relation_ids.add(relation.get("source_id"))
        relation_ids.add(relation.get("target_id"))
    return [
        {
            "id": block["id"],
            "kind": block["kind"],
            "order": block["order"],
            "page": _block_page(block),
            "markdown": block["markdown"],
            "markdown_sha256": _sha256_text(block["markdown"]),
            "visible_text_sha256": _visible_sha256(block["markdown"]),
            "editable": block["kind"] in _EDITABLE_KINDS,
            "in_relations": block["id"] in relation_ids,
        }
        for block in article_model["blocks"]
    ]


def _validate_task_shape(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != _TASK_FIELDS:
        raise ContractValidationError("text task 顶层字段非法")
    if value["contract_version"] not in (
        TEXT_TASK_CONTRACT_VERSION,
        TEXT_TASK_CONTRACT_VERSION_V1,
    ):
        raise ContractValidationError("text task contract_version 不受支持")
    if not isinstance(value["source_sha256"], str) or _HASH_RE.fullmatch(
        value["source_sha256"]
    ) is None:
        raise ContractValidationError("text task source_sha256 非法")

    model = value["article_model"]
    if not isinstance(model, Mapping) or set(model) != _TASK_MODEL_FIELDS:
        raise ContractValidationError("text task article_model 字段非法")
    if model["contract_version"] != ARTICLE_MODEL_CONTRACT_VERSION:
        raise ContractValidationError("text task article model 契约不受支持")
    if not isinstance(model["sha256"], str) or _HASH_RE.fullmatch(
        model["sha256"]
    ) is None:
        raise ContractValidationError("text task article model hash 非法")

    policy = value["policy"]
    if not isinstance(policy, Mapping) or set(policy) != _TASK_POLICY_FIELDS:
        raise ContractValidationError("text task policy 字段非法")
    allowed_operations = policy.get("allowed_operations")
    expected_policy = {
        "text_source": "born-digital-native-pdf",
        "allowed_operations": list(_ALLOWED_OPERATIONS_V1),
        "allowed_change_modes": list(_CHANGE_MODES),
        "immutable_fields": list(_IMMUTABLE_FIELDS),
        "text_equivalence_version": TEXT_EQUIVALENCE_VERSION,
    }
    if policy != expected_policy and not (
        value["contract_version"] == TEXT_TASK_CONTRACT_VERSION
        and policy
        == {
            **expected_policy,
            "allowed_operations": list(_ALLOWED_OPERATIONS_V2),
        }
    ):
        raise ContractValidationError("text task policy 内容非法")
    if (
        allowed_operations not in (list(_ALLOWED_OPERATIONS_V1), list(_ALLOWED_OPERATIONS_V2))
    ):
        raise ContractValidationError("text task allowed_operations 非法")

    blocks = value["blocks"]
    if not isinstance(blocks, list) or not blocks:
        raise ContractValidationError("text task blocks 必须是非空数组")
    seen_ids: set[str] = set()
    for expected_order, block in enumerate(blocks, start=1):
        if not isinstance(block, Mapping) or set(block) != _TASK_BLOCK_FIELDS:
            raise ContractValidationError("text task block 字段非法")
        block_id = block["id"]
        if (
            not isinstance(block_id, str)
            or _BLOCK_ID_RE.fullmatch(block_id) is None
            or block_id in seen_ids
        ):
            raise ContractValidationError("text task block id 非法或重复")
        seen_ids.add(block_id)
        if (
            not isinstance(block["kind"], str)
            or block["kind"] not in VALID_BLOCK_KINDS
        ):
            raise ContractValidationError("text task block kind 非法")
        if (
            isinstance(block["order"], bool)
            or not isinstance(block["order"], int)
            or block["order"] != expected_order
        ):
            raise ContractValidationError("text task block order 必须连续")
        markdown = block["markdown"]
        if not _valid_single_line(markdown):
            raise ContractValidationError("text task markdown 必须是非空单行文本")
        if block["markdown_sha256"] != _sha256_text(markdown):
            raise ContractValidationError("text task markdown hash 不匹配")
        if block["visible_text_sha256"] != _visible_sha256(markdown):
            raise ContractValidationError("text task visible text hash 不匹配")
        if block["editable"] is not (block["kind"] in _EDITABLE_KINDS):
            raise ContractValidationError("text task editable 与 block kind 不一致")


def build_text_task(article_model: Mapping[str, Any]) -> dict[str, Any]:
    """Create a text-only task pinned to one canonical article model."""

    validate_article_model(article_model)
    value = {
        "contract_version": TEXT_TASK_CONTRACT_VERSION,
        "source_sha256": article_model["source_sha256"],
        "article_model": {
            "contract_version": article_model["contract_version"],
            "sha256": _sha256_text(canonical_article_model_json(article_model)),
        },
        "policy": {
            "text_source": "born-digital-native-pdf",
            "allowed_operations": list(_ALLOWED_OPERATIONS_V2),
            "allowed_change_modes": list(_CHANGE_MODES),
            "immutable_fields": list(_IMMUTABLE_FIELDS),
            "text_equivalence_version": TEXT_EQUIVALENCE_VERSION,
        },
        "blocks": _task_blocks(article_model),
    }
    validate_text_task(value, article_model=article_model)
    return value


def validate_text_task(
    value: Mapping[str, Any],
    *,
    article_model: Mapping[str, Any] | None = None,
) -> None:
    """Validate a task and, when supplied, its exact source article model."""

    _validate_task_shape(value)
    if article_model is None:
        return
    validate_article_model(article_model)
    if value["source_sha256"] != article_model["source_sha256"]:
        raise ContractValidationError("text task 与 article model source 不一致")
    expected_hash = _sha256_text(canonical_article_model_json(article_model))
    if value["article_model"] != {
        "contract_version": article_model["contract_version"],
        "sha256": expected_hash,
    }:
        raise ContractValidationError("text task 与 article model hash 不一致")
    if value["blocks"] != _task_blocks(article_model):
        raise ContractValidationError("text task blocks 与 article model 不一致")


def canonical_text_task_json(value: Mapping[str, Any]) -> str:
    validate_text_task(value)
    return _canonical_json(value)


def text_task_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_text(canonical_text_task_json(value))


def _validate_change(old: str, new: str, mode: str) -> None:
    old_heading = _HEADING_PREFIX_RE.match(old)
    new_heading = _HEADING_PREFIX_RE.match(new)
    if (
        old_heading.group(1) if old_heading is not None else None
    ) != (
        new_heading.group(1) if new_heading is not None else None
    ):
        raise ContractValidationError("text review 不允许改变 Markdown 标题层级")
    if len(new) > len(old) + 1024:
        raise ContractValidationError("text review Markdown 格式扩张超过上限")
    if mode == "format-only":
        if normalized_visible_text(old) != normalized_visible_text(new):
            raise ContractValidationError(
                "format-only 不允许改变规范化可见文本"
            )
        return
    if mode == "dehyphenation":
        old_count = len(_DEHYPHENATION_RE.findall(normalized_visible_text(old)))
        new_count = len(_DEHYPHENATION_RE.findall(normalized_visible_text(new)))
        if (
            old_count <= new_count
            or _dehyphenated_visible(old) != _dehyphenated_visible(new)
        ):
            raise ContractValidationError(
                "dehyphenation 只允许删除词内断行连字符与其后空白"
            )
        return
    raise ContractValidationError("text review change_mode 非法")


_SENTENCE_TERMINAL = re.compile(r"[.!?:;]\s*$")


def _join_joiner(previous_markdown: str) -> str:
    """A continuation joins with a space, unless the fragment ends mid-word
    (trailing hyphen) where the split was inside the token."""
    previous = previous_markdown.rstrip()
    return "" if previous.endswith(("-", "‐", "‑")) else " "


def _validate_join_blocks(
    operation: Mapping[str, Any],
    blocks: Mapping[str, dict[str, Any]],
    edited: set[str],
) -> None:
    target = operation.get("target_block_ids")
    if not isinstance(target, list) or len(target) != 2:
        raise ContractValidationError("join-blocks target_block_ids 必须恰好两个")
    if not all(isinstance(item, str) for item in target):
        raise ContractValidationError("join-blocks target_block_ids 非法")
    previous_block = blocks.get(target[0])
    current_block = blocks.get(target[1])
    if previous_block is None or current_block is None:
        raise ContractValidationError("join-blocks block 不存在")
    if target[0] in edited or target[1] in edited:
        raise ContractValidationError("join-blocks block 重复编辑")
    edited.update(target)
    for block in (previous_block, current_block):
        if block["editable"] is not True:
            raise ContractValidationError("join-blocks 不允许编辑视觉槽位")
        if block["kind"] != "body":
            raise ContractValidationError("join-blocks 只允许拼接 body 块")
        if block["in_relations"] is not False:
            raise ContractValidationError(
                "join-blocks 不允许拼接参与关系（figure/caption）的块"
            )
    if previous_block["page"] != current_block["page"]:
        raise ContractValidationError("join-blocks 只允许拼接同页块")
    if abs(previous_block["order"] - current_block["order"]) != 1:
        raise ContractValidationError("join-blocks 只允许拼接阅读顺序相邻的块")
    previous_markdown = previous_block["markdown"]
    current_markdown = current_block["markdown"].lstrip().removeprefix("&emsp;")
    first = current_markdown[:1]
    if not first or not first.islower():
        raise ContractValidationError(
            "join-blocks 续行必须以小写字母开头（当前块不是续行）"
        )
    if _SENTENCE_TERMINAL.search(previous_markdown):
        raise ContractValidationError(
            "join-blocks 上一块以句子终止标点结尾，不允许拼接"
        )
    joiner = _join_joiner(previous_markdown)
    merged = previous_markdown + joiner + current_markdown
    if not _valid_single_line(merged):
        raise ContractValidationError("join-blocks 合并结果必须是非空单行文本")
    reason = operation.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ContractValidationError("join-blocks reason 非法")


def validate_text_review(
    value: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
) -> None:
    """Validate structured edits against a pinned text task."""

    validate_text_task(task)
    if not isinstance(value, Mapping) or set(value) != _REVIEW_FIELDS:
        raise ContractValidationError("text review 顶层字段非法")
    if value["contract_version"] not in (
        TEXT_REVIEW_CONTRACT_VERSION,
        TEXT_REVIEW_CONTRACT_VERSION_V1,
    ):
        raise ContractValidationError("text review contract_version 不受支持")
    if value["task_sha256"] != text_task_sha256(task):
        raise ContractValidationError("text review task hash 不匹配")
    if value["source_sha256"] != task["source_sha256"]:
        raise ContractValidationError("text review source hash 不匹配")
    if value["article_model_sha256"] != task["article_model"]["sha256"]:
        raise ContractValidationError("text review article model hash 不匹配")
    if (
        not isinstance(value["reviewer"], str)
        or not value["reviewer"].strip()
        or len(value["reviewer"]) > 200
    ):
        raise ContractValidationError("text review reviewer 非法")
    operations = value["operations"]
    if not isinstance(operations, list):
        raise ContractValidationError("text review operations 必须是数组")

    blocks = {item["id"]: item for item in task["blocks"]}
    allowed = (
        set(_ALLOWED_OPERATIONS_V2)
        if value["contract_version"] == TEXT_REVIEW_CONTRACT_VERSION
        else set(_ALLOWED_OPERATIONS_V1)
    )
    edited: set[str] = set()
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ContractValidationError("text review operation 非法")
        op = operation.get("op")
        if op not in allowed:
            raise ContractValidationError("text review operation 不受支持")
        if op == "join-blocks":
            if set(operation) != _JOIN_OPERATION_FIELDS:
                raise ContractValidationError("join-blocks operation 字段非法")
            _validate_join_blocks(operation, blocks, edited)
            continue
        if set(operation) != _REPLACE_OPERATION_FIELDS:
            raise ContractValidationError("text review operation 字段非法")
        block_id = operation["block_id"]
        if not isinstance(block_id, str):
            raise ContractValidationError("text review block_id 非法")
        block = blocks.get(block_id)
        if block is None or block_id in edited:
            raise ContractValidationError("text review block 不存在或重复编辑")
        edited.add(block_id)
        if block["editable"] is not True:
            raise ContractValidationError("text review 不允许编辑视觉槽位")
        if operation["expected_markdown_sha256"] != block["markdown_sha256"]:
            raise ContractValidationError("text review block markdown hash 不匹配")
        markdown = operation["markdown"]
        if not _valid_single_line(markdown):
            raise ContractValidationError("text review markdown 必须是非空单行文本")
        if markdown == block["markdown"]:
            raise ContractValidationError("text review 不允许无变化操作")
        reason = operation["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ContractValidationError("text review reason 非法")
        _validate_change(block["markdown"], markdown, operation["change_mode"])


def canonical_text_review_json(
    value: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
) -> str:
    validate_text_review(value, task=task)
    return _canonical_json(value)


def apply_text_review(
    article_model: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply validated Markdown replacements and body joins without changing
    model identity beyond the explicitly allowed join-blocks structure."""

    validate_text_task(task, article_model=article_model)
    validate_text_review(review, task=task)
    result = deepcopy(dict(article_model))
    block_by_id = {block["id"]: block for block in result["blocks"]}
    replacements = {
        operation["block_id"]: operation["markdown"]
        for operation in review["operations"]
        if operation["op"] == "replace-markdown"
    }
    joins = [
        operation["target_block_ids"]
        for operation in review["operations"]
        if operation["op"] == "join-blocks"
    ]
    for block in result["blocks"]:
        replacement = replacements.get(block["id"])
        if replacement is not None:
            block["markdown"] = replacement
    for previous_id, current_id in joins:
        previous = block_by_id[previous_id]
        current = block_by_id[current_id]
        joiner = _join_joiner(previous["markdown"])
        previous["markdown"] = (
            previous["markdown"] + joiner + current["markdown"].lstrip()
        )
        # The head keeps its stable identity (id, source_spans, order): only
        # its text grows. The tail block is removed; its elements remain
        # traceable in the physical_document provenance.
        block_by_id.pop(current_id, None)
    result["blocks"] = [block for block in result["blocks"] if block["id"] in block_by_id]
    for expected_order, block in enumerate(result["blocks"], start=1):
        block["order"] = expected_order
    validate_article_model(result)
    return result
