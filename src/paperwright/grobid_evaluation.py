"""Deterministic corpus validation and metrics for GROBID evidence evaluation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import pypdfium2 as pdfium

from .exceptions import ContractValidationError
from .grobid_provider import GROBID_PROVIDER_ID

GROBID_EVAL_CORPUS_VERSION = "paperwright-grobid-semantic-corpus-v0.1"
GROBID_EVAL_REPORT_VERSION = "paperwright-grobid-semantic-report-v0.1"
GROBID_AUDIT_TASK_VERSION = "paperwright-grobid-claim-audit-task-v0.1"


def canonical_grobid_evaluation_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    raise ContractValidationError(message)


def _native_pdf_stats(path: Path) -> tuple[int, int]:
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001
        _fail(f"evaluation PDF 无法打开: {path}: {exc}")
    character_count = 0
    try:
        for page in document:
            text_page = page.get_textpage()
            try:
                character_count += len(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
        return len(document), character_count
    finally:
        document.close()


def validate_grobid_evaluation_corpus(
    corpus_path: Path,
) -> tuple[dict[str, Any], Path]:
    """Validate the frozen corpus and every bound PDF before model execution."""

    corpus_path = corpus_path.resolve()
    try:
        value = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"evaluation corpus 无法读取: {exc}")
    if not isinstance(value, dict):
        _fail("evaluation corpus 顶层必须是 object")
    if value.get("contract_version") != GROBID_EVAL_CORPUS_VERSION:
        _fail("evaluation corpus contract_version 不支持")
    documents = value.get("documents")
    if not isinstance(documents, list) or not documents:
        _fail("evaluation corpus documents 必须是非空数组")
    root = corpus_path.parent
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    pages = 0
    characters = 0
    total_bytes = 0
    positions: list[int] = []
    for document in documents:
        if not isinstance(document, dict):
            _fail("evaluation corpus document 必须是 object")
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            _fail("evaluation corpus document_id 非法")
        if document_id in seen_ids:
            _fail(f"evaluation corpus document_id 重复: {document_id}")
        seen_ids.add(document_id)
        relative = document.get("file")
        if not isinstance(relative, str) or not relative:
            _fail(f"evaluation corpus file 非法: {document_id}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            _fail(f"evaluation corpus file 越界: {document_id}")
        if not path.is_file():
            _fail(f"evaluation corpus PDF 缺失或 magic 非法: {document_id}")
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                _fail(f"evaluation corpus PDF 缺失或 magic 非法: {document_id}")
        expected_hash = document.get("sha256")
        actual_hash = _sha256(path)
        if expected_hash != actual_hash:
            _fail(f"evaluation corpus PDF hash 不匹配: {document_id}")
        if actual_hash in seen_hashes:
            _fail(f"evaluation corpus PDF hash 重复: {document_id}")
        seen_hashes.add(actual_hash)
        actual_bytes = path.stat().st_size
        if document.get("bytes") != actual_bytes:
            _fail(f"evaluation corpus PDF bytes 不匹配: {document_id}")
        page_count, native_chars = _native_pdf_stats(path)
        if document.get("page_count") != page_count:
            _fail(f"evaluation corpus PDF page_count 不匹配: {document_id}")
        if document.get("native_text_chars") != native_chars:
            _fail(f"evaluation corpus PDF native_text_chars 不匹配: {document_id}")
        position = document.get("candidate_position")
        if not isinstance(position, int) or position < 1:
            _fail(f"evaluation corpus candidate_position 非法: {document_id}")
        positions.append(position)
        pages += page_count
        characters += native_chars
        total_bytes += actual_bytes
    if positions != list(range(positions[0], positions[0] + len(positions))):
        _fail("evaluation corpus candidate_position 必须连续且有序")
    summary = value.get("summary")
    expected_summary = {
        "document_count": len(documents),
        "page_count": pages,
        "native_text_chars": characters,
        "total_bytes": total_bytes,
    }
    if not isinstance(summary, dict) or any(
        summary.get(key) != result for key, result in expected_summary.items()
    ):
        _fail("evaluation corpus summary 与文档清单不一致")
    return value, root


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"evaluation evidence 无法读取 {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"evaluation evidence 顶层必须是 object: {path}")
    return value


def _observation_map(provider: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for page in provider.get("pages", []):
        for observation in page.get("observations", []):
            observation_id = observation.get("observation_id")
            if isinstance(observation_id, str):
                result[observation_id] = {
                    **observation,
                    "_page_index": page.get("page_index"),
                }
    return result


def _character_count(text: object) -> int:
    return len(re.sub(r"\s+", "", text)) if isinstance(text, str) else 0


def summarize_grobid_review(review_root: Path) -> dict[str, Any]:
    """Summarize one immutable layout-prepare evidence directory."""

    evidence = review_root / "source-evidence"
    provider = _load_object(evidence / "providers" / "grobid-scholarly.json")
    claims_value = _load_object(evidence / "claims.json")
    alignments_value = _load_object(evidence / "alignments.json")
    conflicts_value = _load_object(evidence / "conflicts.json")
    requests_value = _load_object(evidence / "specialist-requests.json")
    recipe = _load_object(review_root / "paper-recipe.json")
    observations = _observation_map(provider)
    claims = [
        item
        for item in claims_value.get("claims", [])
        if item.get("provider_id") == GROBID_PROVIDER_ID
    ]
    claim_ids = {item["claim_id"] for item in claims}
    observation_ids = set(observations)
    aligned_ids = {
        item.get("observation_id")
        for item in alignments_value.get("alignments", [])
        if item.get("provider_id") == GROBID_PROVIDER_ID
    } & observation_ids
    best_text_scores: dict[str, float] = {}
    for alignment in alignments_value.get("alignments", []):
        if alignment.get("provider_id") != GROBID_PROVIDER_ID:
            continue
        observation_id = alignment.get("observation_id")
        score = alignment.get("text_score")
        if observation_id in observation_ids and isinstance(score, (int, float)):
            best_text_scores[observation_id] = max(
                float(score), best_text_scores.get(observation_id, 0.0)
            )
    type_rows: dict[str, dict[str, Any]] = {}
    for claim_type in sorted({item["claim_type"] for item in claims}):
        typed = [item for item in claims if item["claim_type"] == claim_type]
        typed_observations = {
            observation_id
            for item in typed
            for observation_id in item.get("evidence_observation_ids", [])
            if observation_id in observations
        }
        typed_aligned = typed_observations & aligned_ids
        observed_chars = sum(
            _character_count(observations[item].get("text"))
            for item in typed_observations
        )
        aligned_chars = sum(
            _character_count(observations[item].get("text"))
            for item in typed_aligned
        )
        weighted_chars = sum(
            _character_count(observations[item].get("text"))
            * best_text_scores.get(item, 0.0)
            for item in typed_observations
        )
        type_rows[claim_type] = {
            "claim_count": len(typed),
            "observation_count": len(typed_observations),
            "aligned_observation_count": len(typed_aligned),
            "native_alignment_support": (
                round(len(typed_aligned) / len(typed_observations), 6)
                if typed_observations
                else "not_applicable"
            ),
            "observed_character_count": observed_chars,
            "aligned_character_count": aligned_chars,
            "alignment_weighted_character_count": round(weighted_chars, 6),
            "aligned_character_coverage": (
                round(aligned_chars / observed_chars, 6)
                if observed_chars
                else "not_applicable"
            ),
            "alignment_weighted_text_coverage": (
                round(weighted_chars / observed_chars, 6)
                if observed_chars
                else "not_applicable"
            ),
        }
    related_conflicts = [
        item
        for item in conflicts_value.get("conflicts", [])
        if claim_ids.intersection(item.get("claim_ids", []))
        or observation_ids.intersection(item.get("observation_ids", []))
    ]
    related_actions = [
        item
        for item in recipe.get("actions", [])
        if (claim_ids | observation_ids).intersection(item.get("evidence_refs", []))
    ]
    related_conflict_ids = {item.get("conflict_id") for item in related_conflicts}
    related_requests = [
        item
        for item in requests_value.get("requests", [])
        if item.get("conflict_id") in related_conflict_ids
    ]
    return {
        "provider_status": provider.get("status"),
        "provider_version": provider.get("provider_version"),
        "observation_count": len(observations),
        "aligned_observation_count": len(aligned_ids),
        "claim_count": len(claims),
        "claim_counts_by_type": dict(sorted(Counter(
            item["claim_type"] for item in claims
        ).items())),
        "by_claim_type": type_rows,
        "all_conflict_count": len(conflicts_value.get("conflicts", [])),
        "grobid_related_conflict_count": len(related_conflicts),
        "all_specialist_request_count": len(requests_value.get("requests", [])),
        "grobid_related_specialist_request_count": len(related_requests),
        "all_recipe_action_count": len(recipe.get("actions", [])),
        "grobid_referenced_recipe_action_count": len(related_actions),
        "grobid_referenced_recipe_roles": dict(sorted(Counter(
            item.get("role", "none") for item in related_actions
        ).items())),
    }


def aggregate_grobid_evidence_summaries(
    summaries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate fixed per-document metrics as micro and document-macro views."""

    claim_types = sorted({
        claim_type
        for summary in summaries
        for claim_type in summary.get("by_claim_type", {})
    })
    result: dict[str, Any] = {}
    for claim_type in claim_types:
        rows = [
            summary["by_claim_type"][claim_type]
            for summary in summaries
            if claim_type in summary.get("by_claim_type", {})
        ]
        observations = sum(int(row["observation_count"]) for row in rows)
        aligned = sum(int(row["aligned_observation_count"]) for row in rows)
        observed_chars = sum(int(row["observed_character_count"]) for row in rows)
        aligned_chars = sum(int(row["aligned_character_count"]) for row in rows)
        weighted_chars = sum(
            float(row["alignment_weighted_character_count"]) for row in rows
        )
        support_values = [
            float(row["native_alignment_support"])
            for row in rows
            if isinstance(row["native_alignment_support"], (int, float))
        ]
        aligned_character_values = [
            float(row["aligned_character_coverage"])
            for row in rows
            if isinstance(row["aligned_character_coverage"], (int, float))
        ]
        weighted_values = [
            float(row["alignment_weighted_text_coverage"])
            for row in rows
            if isinstance(row["alignment_weighted_text_coverage"], (int, float))
        ]
        result[claim_type] = {
            "document_count": len(rows),
            "claim_count": sum(int(row["claim_count"]) for row in rows),
            "observation_count": observations,
            "aligned_observation_count": aligned,
            "native_alignment_support_micro": (
                round(aligned / observations, 6)
                if observations
                else "not_applicable"
            ),
            "native_alignment_support_document_macro": (
                round(sum(support_values) / len(support_values), 6)
                if support_values
                else "not_applicable"
            ),
            "observed_character_count": observed_chars,
            "aligned_character_count": aligned_chars,
            "aligned_character_coverage_micro": (
                round(aligned_chars / observed_chars, 6)
                if observed_chars
                else "not_applicable"
            ),
            "aligned_character_coverage_document_macro": (
                round(sum(aligned_character_values) / len(aligned_character_values), 6)
                if aligned_character_values
                else "not_applicable"
            ),
            "alignment_weighted_character_count": round(weighted_chars, 6),
            "alignment_weighted_text_coverage_micro": (
                round(weighted_chars / observed_chars, 6)
                if observed_chars
                else "not_applicable"
            ),
            "alignment_weighted_text_coverage_document_macro": (
                round(sum(weighted_values) / len(weighted_values), 6)
                if weighted_values
                else "not_applicable"
            ),
        }
    return result


def build_grobid_audit_task(
    review_root: Path,
    *,
    document_id: str,
    source_sha256: str,
    page_image_path_prefix: str = "",
) -> dict[str, Any]:
    """Build a claim audit task without exposing downstream adoption decisions."""

    evidence = review_root / "source-evidence"
    provider = _load_object(evidence / "providers" / "grobid-scholarly.json")
    claims_value = _load_object(evidence / "claims.json")
    alignments_value = _load_object(evidence / "alignments.json")
    observations = _observation_map(provider)
    alignment_by_observation: dict[str, list[dict[str, Any]]] = {}
    for alignment in alignments_value.get("alignments", []):
        if alignment.get("provider_id") == GROBID_PROVIDER_ID:
            alignment_by_observation.setdefault(
                alignment.get("observation_id"), []
            ).append(alignment)
    items = []
    used_pages: set[int] = set()
    for claim in claims_value.get("claims", []):
        if claim.get("provider_id") != GROBID_PROVIDER_ID:
            continue
        segments = []
        for observation_id in claim.get("evidence_observation_ids", []):
            observation = observations.get(observation_id)
            if observation is None:
                continue
            matches = alignment_by_observation.get(observation_id, [])
            page_index = observation.get("_page_index")
            if isinstance(page_index, int):
                used_pages.add(page_index)
            segments.append(
                {
                    "observation_id": observation_id,
                    "page_index": page_index,
                    "paperwright_bbox": observation.get("paperwright_bbox"),
                    "text": observation.get("text"),
                    "alignments": [
                        {
                            "physical_element_id": item.get("physical_element_id"),
                            "text_score": item.get("text_score"),
                            "geometry_score": item.get("geometry_score"),
                        }
                        for item in matches
                    ],
                }
            )
        items.append(
            {
                "claim_id": claim.get("claim_id"),
                "claim_type": claim.get("claim_type"),
                "segments": segments,
            }
        )
    page_images = []
    for page_index in sorted(used_pages):
        relative = Path(f"page-{page_index + 1:04d}") / "page.png"
        image_path = review_root / relative
        if not image_path.is_file():
            _fail(f"GROBID audit page image 缺失: {image_path}")
        exposed_path = (
            Path(page_image_path_prefix) / relative
            if page_image_path_prefix
            else relative
        )
        page_images.append(
            {
                "page_index": page_index,
                "path": exposed_path.as_posix(),
                "sha256": _sha256(image_path),
            }
        )
    return {
        "contract_version": GROBID_AUDIT_TASK_VERSION,
        "document_id": document_id,
        "source_sha256": source_sha256,
        "provider_id": GROBID_PROVIDER_ID,
        "provider_version": provider.get("provider_version"),
        "claim_count": len(items),
        "claims": items,
        "page_images": page_images,
        "review_labels": [
            "correct",
            "partial",
            "wrong_role",
            "unsupported",
            "uncertain",
        ],
        "downstream_adoption_disclosed": False,
    }


def compare_grobid_review_summaries(
    native: Mapping[str, Any],
    grobid: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose downstream count deltas without treating them as quality gains."""

    keys = (
        "all_conflict_count",
        "all_specialist_request_count",
        "all_recipe_action_count",
    )
    return {
        "count_deltas_grobid_minus_native": {
            key: int(grobid[key]) - int(native[key]) for key in keys
        },
        "grobid_referenced_recipe_action_count": grobid[
            "grobid_referenced_recipe_action_count"
        ],
        "quality_improvement_inferred": False,
    }


__all__ = [
    "GROBID_AUDIT_TASK_VERSION",
    "GROBID_EVAL_CORPUS_VERSION",
    "GROBID_EVAL_REPORT_VERSION",
    "aggregate_grobid_evidence_summaries",
    "build_grobid_audit_task",
    "canonical_grobid_evaluation_json",
    "compare_grobid_review_summaries",
    "summarize_grobid_review",
    "validate_grobid_evaluation_corpus",
]
