"""Contracts for evidence-backed caption/visual relation annotations.

Real PDFs and rendered pages stay outside the source repository.  The dataset
stores only document/page hashes, short caption anchors, structural labels and
adjudication provenance so every example can be rechecked against local
evidence without committing paper content.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping

from .exceptions import ContractValidationError


RELATION_DATASET_VERSION = "paperwright-caption-relation-dataset-v0.1"
RELATION_DATASET_SCOPE = "scientific-paper-caption-visual-relations"

_HASH = re.compile(r"^[0-9a-f]{64}$")
_LABELS = {"positive", "negative", "uncertain"}
_KINDS = {"figure", "table"}
_CONFIDENCE = {"high", "medium", "low"}
_STATUSES = {"seed_verified", "human_verified"}
_RATIONALE_CODES = {
    "explicit_see_next_page",
    "full_page_visual_then_caption",
    "same_page_visual",
    "previous_page_no_visual",
    "ambiguous_page_evidence",
}


def relation_dataset_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    documents = value.get("documents", [])
    examples = value.get("examples", [])
    by_label = Counter(item.get("label") for item in examples)
    by_kind = Counter(item.get("caption_kind") for item in examples)
    positive_documents = {
        item.get("document_id")
        for item in examples
        if item.get("label") == "positive"
    }
    return {
        "document_count": len(documents),
        "example_count": len(examples),
        "positive_document_count": len(positive_documents),
        "by_label": {
            label: int(by_label.get(label, 0))
            for label in sorted(_LABELS)
        },
        "by_kind": {
            kind: int(by_kind.get(kind, 0))
            for kind in sorted(_KINDS)
        },
    }


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def validate_relation_dataset(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "dataset_id",
        "quality_tier",
        "scope",
        "documents",
        "examples",
        "summary",
    }:
        raise ContractValidationError("relation dataset 顶层字段非法")
    if (
        value["contract_version"] != RELATION_DATASET_VERSION
        or value["scope"] != RELATION_DATASET_SCOPE
        or value["quality_tier"] not in {"silver", "gold"}
        or not isinstance(value["dataset_id"], str)
        or not value["dataset_id"]
    ):
        raise ContractValidationError("relation dataset 身份非法")

    documents = value["documents"]
    if not isinstance(documents, list) or not documents:
        raise ContractValidationError("relation dataset documents 必须是非空数组")
    document_by_id: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        if not isinstance(document, Mapping) or set(document) != {
            "document_id",
            "source_sha256",
            "page_count",
        }:
            raise ContractValidationError("relation dataset document 字段非法")
        document_id = document["document_id"]
        if (
            not isinstance(document_id, str)
            or not document_id
            or document_id in document_by_id
            or not _is_hash(document["source_sha256"])
            or type(document["page_count"]) is not int
            or document["page_count"] <= 0
        ):
            raise ContractValidationError("relation dataset document 内容非法")
        document_by_id[document_id] = document

    examples = value["examples"]
    if not isinstance(examples, list) or not examples:
        raise ContractValidationError("relation dataset examples 必须是非空数组")
    example_ids: set[str] = set()
    for example in examples:
        if not isinstance(example, Mapping) or set(example) != {
            "example_id",
            "document_id",
            "relation_scope",
            "visual_page_index",
            "caption_page_index",
            "caption_kind",
            "label",
            "caption_evidence",
            "page_evidence",
            "adjudication",
        }:
            raise ContractValidationError("relation dataset example 字段非法")
        example_id = example["example_id"]
        document_id = example["document_id"]
        if (
            not isinstance(example_id, str)
            or not example_id
            or example_id in example_ids
            or document_id not in document_by_id
            or example["relation_scope"] != "cross_page_adjacent"
            or type(example["visual_page_index"]) is not int
            or type(example["caption_page_index"]) is not int
            or example["caption_page_index"] != example["visual_page_index"] + 1
            or example["caption_kind"] not in _KINDS
            or example["label"] not in _LABELS
        ):
            raise ContractValidationError("relation dataset example 内容非法")
        page_count = int(document_by_id[document_id]["page_count"])
        if not (
            0 <= example["visual_page_index"] < page_count
            and 0 <= example["caption_page_index"] < page_count
        ):
            raise ContractValidationError("relation dataset example 页码越界")
        example_ids.add(example_id)

        caption = example["caption_evidence"]
        if (
            not isinstance(caption, Mapping)
            or set(caption)
            != {"anchor_element_id", "text_prefix", "text_sha256", "normalized_y"}
            or not isinstance(caption["anchor_element_id"], str)
            or not caption["anchor_element_id"]
            or not isinstance(caption["text_prefix"], str)
            or not 1 <= len(caption["text_prefix"]) <= 160
            or not _is_hash(caption["text_sha256"])
            or not isinstance(caption["normalized_y"], (int, float))
            or isinstance(caption["normalized_y"], bool)
            or not 0.0 <= float(caption["normalized_y"]) <= 1.0
        ):
            raise ContractValidationError("relation dataset caption evidence 非法")

        page_evidence = example["page_evidence"]
        if (
            not isinstance(page_evidence, Mapping)
            or set(page_evidence)
            != {
                "visual_page_image_sha256",
                "caption_page_image_sha256",
                "signals",
            }
            or not _is_hash(page_evidence["visual_page_image_sha256"])
            or not _is_hash(page_evidence["caption_page_image_sha256"])
            or not isinstance(page_evidence["signals"], list)
            or not page_evidence["signals"]
            or any(
                not isinstance(item, str) or not item
                for item in page_evidence["signals"]
            )
            or len(page_evidence["signals"])
            != len(set(page_evidence["signals"]))
        ):
            raise ContractValidationError("relation dataset page evidence 非法")

        adjudication = example["adjudication"]
        if (
            not isinstance(adjudication, Mapping)
            or set(adjudication)
            != {
                "status",
                "reviewer",
                "confidence",
                "rationale_code",
                "source_annotation",
            }
            or adjudication["status"] not in _STATUSES
            or not isinstance(adjudication["reviewer"], str)
            or not adjudication["reviewer"]
            or adjudication["confidence"] not in _CONFIDENCE
            or adjudication["rationale_code"] not in _RATIONALE_CODES
            or not isinstance(adjudication["source_annotation"], str)
            or not adjudication["source_annotation"]
        ):
            raise ContractValidationError("relation dataset adjudication 非法")
        if value["quality_tier"] == "gold" and adjudication["status"] != "human_verified":
            raise ContractValidationError("gold relation dataset 只能包含人工确认样本")

    if value["summary"] != relation_dataset_summary(value):
        raise ContractValidationError("relation dataset summary 不守恒")


__all__ = [
    "RELATION_DATASET_SCOPE",
    "RELATION_DATASET_VERSION",
    "relation_dataset_summary",
    "validate_relation_dataset",
]
