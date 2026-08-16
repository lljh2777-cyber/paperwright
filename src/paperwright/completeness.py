"""Page and visual completeness checks shared by all output writers.

The gate does not infer scientific content.  It compares native PDF evidence
with projected text/assets, preserves image-only pages through deterministic
full-page rendering, and makes unresolved loss explicit.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .exceptions import ContractValidationError
from .models import BBox, Page, PhysicalDocument
from .region_render import RegionRenderRequest


COMPLETENESS_CONTRACT_VERSION = "paperwright-completeness-v0.1"
COMPLETENESS_REPORT_PATH = "_paperwright/completeness-report.json"
COMPLETENESS_STATES = {
    "accepted",
    "suspicious",
    "human_required",
    "invalid",
}
_CAPTION_PREFIXES = ("figure", "fig.", "fig ", "table")


def canonical_completeness_json(value: Mapping[str, Any]) -> str:
    validate_completeness_report(value)
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


def _usable_text_count(page: Page) -> int:
    return sum(
        item.kind == "text"
        and bool((item.text or "").strip())
        and not item.metadata.get("markdown_excluded_reason")
        for item in page.elements
    )


def _caption_like_count(page: Page) -> int:
    return sum(
        item.kind == "text"
        and bool((text := (item.text or "").strip().casefold()))
        and text.startswith(_CAPTION_PREFIXES)
        for item in page.elements
    )


def page_requires_visual_fallback(
    page: Page,
    *,
    projected_text_count: int,
    projected_visual_count: int,
) -> bool:
    """Return true only for non-blank pages with no usable native text/output."""

    if projected_text_count > 0 or projected_visual_count > 0:
        return False
    if _usable_text_count(page) > 0:
        return False
    return any(item.kind in {"image", "vector"} for item in page.elements)


def full_page_render_request(
    page: Page,
    *,
    reason: str = "native_text_missing_full_page_fallback",
    scale: float = 1.5,
) -> RegionRenderRequest:
    if scale <= 0:
        raise ValueError("full-page fallback scale must be positive")
    identifier = f"page-fallback-p{page.page_index + 1:04d}"
    return RegionRenderRequest(
        figure_id=identifier,
        page_index=page.page_index,
        bbox=BBox(0.0, 0.0, page.width, page.height),
        caption_top=page.height + 1.0,
        caption_id=f"{identifier}-none",
        caption_element_ids=(),
        caption_text="",
        caption_bbox=BBox(0.0, max(0.0, page.height - 1.0), 1.0, 1.0),
        caption_reason="page_fallback_has_no_caption",
        caption_confidence=1.0,
        member_element_ids=tuple(
            item.element_id
            for item in page.elements
            if item.kind in {"image", "vector"}
        )[:256],
        vector_evidence_element_ids=tuple(
            item.element_id for item in page.elements if item.kind == "vector"
        )[:128],
        vector_evidence_count=sum(
            item.kind == "vector" for item in page.elements
        ),
        vector_evidence_sha256=hashlib.sha256(
            "\n".join(
                item.element_id for item in page.elements if item.kind == "vector"
            ).encode("utf-8")
        ).hexdigest(),
        fallback_reason=reason,
        bbox_rule="complete_source_page_bounds",
        scale=scale,
        dpi=72.0 * scale,
        max_page_area_ratio=1.01,
        min_variance=0.5,
        caption_guard=0.0,
    )


def build_completeness_report(
    document: PhysicalDocument,
    *,
    projected_text_counts: Mapping[int, int],
    projected_visual_counts: Mapping[int, int],
    fallback_pages: Sequence[int] = (),
    unresolved_pages: Mapping[int, str] | None = None,
    orphan_caption_counts: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    fallback = set(fallback_pages)
    unresolved = dict(unresolved_pages or {})
    orphan_captions = dict(orphan_caption_counts or {})
    pages: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    states: Counter[str] = Counter()

    for page in document.pages:
        page_index = page.page_index
        native_text_count = _usable_text_count(page)
        native_image_count = sum(item.kind == "image" for item in page.elements)
        native_vector_count = sum(item.kind == "vector" for item in page.elements)
        caption_like_count = _caption_like_count(page)
        projected_text = int(projected_text_counts.get(page_index, 0))
        projected_visual = int(projected_visual_counts.get(page_index, 0))
        orphan_caption_count = int(orphan_captions.get(page_index, 0))
        source_blank = (
            native_text_count == 0
            and native_image_count == 0
            and native_vector_count == 0
        )
        reasons: list[str] = []

        if page_index in unresolved:
            state = "human_required"
            reasons.append(unresolved[page_index])
        elif projected_text == 0 and projected_visual == 0:
            if source_blank:
                state = "accepted"
                reasons.append("source_page_blank")
            elif native_text_count > 0:
                state = "invalid"
                reasons.append("native_text_not_projected")
            else:
                state = "human_required"
                reasons.append("native_non_text_page_not_projected")
        else:
            state = "accepted"
            if page_index in fallback:
                reasons.append("full_page_fallback_rendered")

        if orphan_caption_count > 0:
            if state == "accepted":
                state = "suspicious"
            reasons.append("caption_without_bound_visual")
            findings.append(
                {
                    "code": "caption_without_bound_visual",
                    "page": page_index + 1,
                    "count": orphan_caption_count,
                }
            )
        if (
            native_vector_count >= 8
            and caption_like_count > 0
            and projected_visual == 0
        ):
            if state == "accepted":
                state = "suspicious"
            reasons.append("vector_dense_caption_page_without_visual")
            findings.append(
                {
                    "code": "vector_dense_caption_page_without_visual",
                    "page": page_index + 1,
                    "vector_count": native_vector_count,
                    "caption_like_count": caption_like_count,
                }
            )
        if state in {"invalid", "human_required"}:
            findings.append(
                {
                    "code": reasons[0],
                    "page": page_index + 1,
                    "native_text_count": native_text_count,
                    "native_image_count": native_image_count,
                    "native_vector_count": native_vector_count,
                }
            )

        reasons = list(dict.fromkeys(reasons))
        states[state] += 1
        pages.append(
            {
                "page_index": page_index,
                "state": state,
                "reasons": reasons,
                "source": {
                    "usable_text_element_count": native_text_count,
                    "image_element_count": native_image_count,
                    "vector_element_count": native_vector_count,
                    "caption_like_count": caption_like_count,
                    "blank": source_blank,
                },
                "projection": {
                    "text_block_count": projected_text,
                    "visual_asset_count": projected_visual,
                    "full_page_fallback": page_index in fallback,
                    "orphan_caption_count": orphan_caption_count,
                },
            }
        )

    status = (
        "fail"
        if states["invalid"]
        else "warning"
        if states["suspicious"] or states["human_required"]
        else "pass"
    )
    report = {
        "contract_version": COMPLETENESS_CONTRACT_VERSION,
        "source_sha256": document.source_sha256,
        "status": status,
        "page_count": len(document.pages),
        "summary": {
            "accepted": states["accepted"],
            "suspicious": states["suspicious"],
            "human_required": states["human_required"],
            "invalid": states["invalid"],
            "full_page_fallback": len(fallback),
        },
        "pages": pages,
        "findings": findings,
    }
    validate_completeness_report(report)
    return report


def completeness_manifest_record(
    report: Mapping[str, Any],
    *,
    report_sha256: str,
) -> dict[str, Any]:
    validate_completeness_report(report)
    return {
        "contract_version": COMPLETENESS_CONTRACT_VERSION,
        "status": report["status"],
        "report_path": COMPLETENESS_REPORT_PATH,
        "report_sha256": report_sha256,
        **report["summary"],
    }


def validate_completeness_report(value: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "source_sha256",
        "status",
        "page_count",
        "summary",
        "pages",
        "findings",
    }
    if set(value) != required:
        raise ContractValidationError("completeness report 顶层字段非法")
    if value["contract_version"] != COMPLETENESS_CONTRACT_VERSION:
        raise ContractValidationError("completeness report 契约版本非法")
    source_sha256 = value["source_sha256"]
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise ContractValidationError("completeness report source hash 非法")
    if value["status"] not in {"pass", "warning", "fail"}:
        raise ContractValidationError("completeness report status 非法")
    if not isinstance(value["page_count"], int) or value["page_count"] <= 0:
        raise ContractValidationError("completeness report page_count 非法")
    summary = value["summary"]
    summary_fields = {
        "accepted",
        "suspicious",
        "human_required",
        "invalid",
        "full_page_fallback",
    }
    if not isinstance(summary, Mapping) or set(summary) != summary_fields:
        raise ContractValidationError("completeness report summary 非法")
    if any(type(summary[name]) is not int or summary[name] < 0 for name in summary):
        raise ContractValidationError("completeness report summary count 非法")
    if sum(summary[name] for name in COMPLETENESS_STATES) != value["page_count"]:
        raise ContractValidationError("completeness report state count 与页数不一致")
    expected_status = (
        "fail"
        if summary["invalid"]
        else "warning"
        if summary["suspicious"] or summary["human_required"]
        else "pass"
    )
    if value["status"] != expected_status:
        raise ContractValidationError("completeness report status 与 summary 不一致")
    pages = value["pages"]
    if not isinstance(pages, list) or len(pages) != value["page_count"]:
        raise ContractValidationError("completeness report pages 非法")
    state_counts: Counter[str] = Counter()
    fallback_count = 0
    source_fields = {
        "usable_text_element_count",
        "image_element_count",
        "vector_element_count",
        "caption_like_count",
        "blank",
    }
    projection_fields = {
        "text_block_count",
        "visual_asset_count",
        "full_page_fallback",
        "orphan_caption_count",
    }
    for expected_index, page in enumerate(pages):
        if (
            not isinstance(page, Mapping)
            or set(page) != {"page_index", "state", "reasons", "source", "projection"}
            or page["page_index"] != expected_index
            or page["state"] not in COMPLETENESS_STATES
            or not isinstance(page["reasons"], list)
            or not isinstance(page["source"], Mapping)
            or not isinstance(page["projection"], Mapping)
        ):
            raise ContractValidationError("completeness report page record 非法")
        if any(
            not isinstance(reason, str) or not reason
            for reason in page["reasons"]
        ):
            raise ContractValidationError("completeness report page reasons 非法")
        source = page["source"]
        projection = page["projection"]
        if set(source) != source_fields or set(projection) != projection_fields:
            raise ContractValidationError("completeness report page evidence 非法")
        for name in source_fields - {"blank"}:
            if type(source[name]) is not int or source[name] < 0:
                raise ContractValidationError("completeness report source count 非法")
        for name in projection_fields - {"full_page_fallback"}:
            if type(projection[name]) is not int or projection[name] < 0:
                raise ContractValidationError("completeness report projection count 非法")
        if type(source["blank"]) is not bool or type(
            projection["full_page_fallback"]
        ) is not bool:
            raise ContractValidationError("completeness report page boolean 非法")
        state_counts[page["state"]] += 1
        fallback_count += int(projection["full_page_fallback"])
    if any(state_counts[name] != summary[name] for name in COMPLETENESS_STATES):
        raise ContractValidationError("completeness report page states 与 summary 不一致")
    if fallback_count != summary["full_page_fallback"]:
        raise ContractValidationError("completeness report fallback count 不一致")
    if not isinstance(value["findings"], list) or any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("code"), str)
        or not item["code"]
        or type(item.get("page")) is not int
        or not 1 <= item["page"] <= value["page_count"]
        for item in value["findings"]
    ):
        raise ContractValidationError("completeness report findings 非法")


def validate_completeness_manifest_record(
    value: Mapping[str, Any],
    *,
    outputs: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    required = {
        "contract_version",
        "status",
        "report_path",
        "report_sha256",
        "accepted",
        "suspicious",
        "human_required",
        "invalid",
        "full_page_fallback",
    }
    if set(value) != required:
        raise ContractValidationError("manifest completeness 字段非法")
    if value["contract_version"] != COMPLETENESS_CONTRACT_VERSION:
        raise ContractValidationError("manifest completeness 契约非法")
    if value["status"] not in {"pass", "warning", "fail"}:
        raise ContractValidationError("manifest completeness status 非法")
    path = PurePosixPath(value["report_path"])
    if str(path) != COMPLETENESS_REPORT_PATH or path.is_absolute() or ".." in path.parts:
        raise ContractValidationError("manifest completeness report path 非法")
    digest = value["report_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ContractValidationError("manifest completeness report hash 非法")
    for name in COMPLETENESS_STATES | {"full_page_fallback"}:
        if type(value[name]) is not int or value[name] < 0:
            raise ContractValidationError("manifest completeness count 非法")
    total = sum(value[name] for name in COMPLETENESS_STATES)
    expected_status = (
        "fail"
        if value["invalid"]
        else "warning"
        if value["suspicious"] or value["human_required"]
        else "pass"
    )
    if total <= 0 or value["full_page_fallback"] > total:
        raise ContractValidationError("manifest completeness count 不一致")
    if value["status"] != expected_status:
        raise ContractValidationError("manifest completeness status 不一致")
    if outputs is not None:
        record = next(
            (item for item in outputs if item.get("path") == COMPLETENESS_REPORT_PATH),
            None,
        )
        if (
            record is None
            or record.get("role") != "completeness_report"
            or record.get("sha256") != digest
        ):
            raise ContractValidationError(
                "manifest completeness 与 outputs 清单不一致"
            )


__all__ = [
    "COMPLETENESS_CONTRACT_VERSION",
    "COMPLETENESS_REPORT_PATH",
    "build_completeness_report",
    "canonical_completeness_json",
    "completeness_manifest_record",
    "full_page_render_request",
    "page_requires_visual_fallback",
    "validate_completeness_manifest_record",
    "validate_completeness_report",
]
