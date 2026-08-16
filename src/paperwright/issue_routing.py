"""Deterministic issue-level routing for the hybrid scientific-paper pipeline.

The base page operation is always L0.  Escalation belongs to a concrete,
source-backed issue (text continuation, visual ambiguity, caption binding, or
page preservation), never to the page as an indivisible semantic unit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence

from .exceptions import ContractValidationError
from .completeness import validate_completeness_report
from .layout_models import LayoutTask, NormalizedBBox
from .layout_risk import LayoutRiskAssessment
from .article_model import validate_article_model
from .models import Element, Page, PhysicalDocument
from .text_review import join_candidates, validate_text_task
from .routing import (
    ROUTE_HUMAN_REVIEW,
    ROUTE_L0_RULE,
    ROUTE_L1_TEXT_MODEL,
    ROUTE_L2_VISUAL_MODEL,
    ROUTE_L3_PROGRAM_SYNTHESIS,
)


ISSUE_ROUTING_CONTRACT_VERSION = "paperwright-issue-routing-v0.1"
ISSUE_STATUS_OPEN = "open"

ISSUE_PAGE_VISUAL_PRESERVATION = "page_visual_preservation"
ISSUE_NATIVE_TEXT_EVIDENCE_MISSING = "native_text_evidence_missing"
ISSUE_LAYOUT_GEOMETRY_AMBIGUITY = "layout_geometry_ambiguity"
ISSUE_CAPTION_VISUAL_BINDING = "caption_visual_binding"
ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING = (
    "cross_page_caption_visual_binding"
)
ISSUE_PARAGRAPH_CONTINUATION = "paragraph_continuation"

_ISSUE_KINDS = {
    ISSUE_PAGE_VISUAL_PRESERVATION,
    ISSUE_NATIVE_TEXT_EVIDENCE_MISSING,
    ISSUE_LAYOUT_GEOMETRY_AMBIGUITY,
    ISSUE_CAPTION_VISUAL_BINDING,
    ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING,
    ISSUE_PARAGRAPH_CONTINUATION,
}
_STAGES = {"layout", "text", "projection"}
_SEVERITIES = {"required", "suspicious"}
_SCOPE_TYPES = {"page", "bbox", "elements", "candidates"}
_ROUTES = {
    ROUTE_L0_RULE,
    ROUTE_L1_TEXT_MODEL,
    ROUTE_L2_VISUAL_MODEL,
    ROUTE_L3_PROGRAM_SYNTHESIS,
    ROUTE_HUMAN_REVIEW,
}
_ROUTE_PRIORITY = {
    ROUTE_L0_RULE: 0,
    ROUTE_L1_TEXT_MODEL: 1,
    ROUTE_L3_PROGRAM_SYNTHESIS: 2,
    ROUTE_L2_VISUAL_MODEL: 3,
    ROUTE_HUMAN_REVIEW: 4,
}
_FIGURE_CAPTION = re.compile(
    r"^\s*fig(?:ure)?\.?\s+S?\d+[A-Za-z]?\s*(?:[|.:]|$)",
    re.IGNORECASE,
)
_NEXT_PAGE_CAPTION_MARKER = re.compile(
    r"(?:see|continued?\s+on)\s+(?:the\s+)?next\s+page.{0,40}(?:caption|legend)|"
    r"(?:caption|legend).{0,40}(?:on|at)\s+(?:the\s+)?next\s+page",
    re.IGNORECASE,
)
_PREVIOUS_PAGE_MARKER = re.compile(
    r"(?:continued?\s+from\s+(?:the\s+)?previous\s+page|^[◀◁←])",
    re.IGNORECASE,
)


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


def _usable_text_elements(page: Page) -> tuple[Element, ...]:
    return tuple(
        item
        for item in page.elements
        if item.kind == "text"
        and bool((item.text or "").strip())
        and not item.metadata.get("markdown_excluded_reason")
    )


def _task_roi(task: LayoutTask) -> dict[str, float]:
    raw = task.metadata.get("analysis_roi")
    if isinstance(raw, Mapping) and isinstance(raw.get("bbox"), Mapping):
        return NormalizedBBox.from_dict(raw["bbox"]).to_dict()
    return {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def _raster_region_count(task: LayoutTask) -> int | None:
    evidence = task.metadata.get("raster_evidence")
    if not isinstance(evidence, Mapping):
        return None
    value = evidence.get("region_count")
    return value if type(value) is int and value >= 0 else None


def _union_normalized_bboxes(
    boxes: Sequence[Mapping[str, Any]],
) -> dict[str, float] | None:
    if not boxes:
        return None
    left = min(float(item["x"]) for item in boxes)
    top = min(float(item["y"]) for item in boxes)
    right = max(float(item["x"]) + float(item["width"]) for item in boxes)
    bottom = max(float(item["y"]) + float(item["height"]) for item in boxes)
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def _caption_text_elements(page: Page) -> tuple[Element, ...]:
    return tuple(
        item
        for item in _usable_text_elements(page)
        if _FIGURE_CAPTION.match(item.text or "")
    )


def _normalized_element_bbox(page: Page, element: Element) -> dict[str, float]:
    return {
        "x": element.bbox.x / page.width,
        "y": element.bbox.y / page.height,
        "width": element.bbox.width / page.width,
        "height": element.bbox.height / page.height,
    }


def _cross_page_visual_candidates(task: LayoutTask) -> tuple[str, ...]:
    result: list[str] = []
    for candidate in task.candidates:
        features = candidate.features
        likely_visual = (
            "raster" in candidate.element_kinds
            or "image" in candidate.element_kinds
            or int(features.get("image_count") or 0) > 0
            or int(features.get("drawing_count") or 0) >= 4
            or float(features.get("drawing_coverage") or 0.0) >= 0.12
            or int(features.get("panel_label_count") or 0) >= 2
        )
        if (
            likely_visual
            and candidate.bbox.bottom >= 0.68
            and candidate.bbox.width * candidate.bbox.height >= 0.06
        ):
            result.append(candidate.candidate_id)
    return tuple(result)


def _has_text_marker(page: Page, pattern: re.Pattern[str]) -> bool:
    return any(
        pattern.search(" ".join((item.text or "").split())) is not None
        for item in _usable_text_elements(page)
    )


def _caption_has_previous_page_marker(page: Page, caption: Element) -> bool:
    for item in _usable_text_elements(page):
        text = " ".join((item.text or "").split())
        if _PREVIOUS_PAGE_MARKER.search(text) is None:
            continue
        if text.casefold().startswith(("continued", "continue")):
            return True
        same_line = abs(item.bbox.y - caption.bbox.y) / page.height <= 0.04
        if same_line and item.bbox.x <= caption.bbox.x:
            return True
    return False


def _visual_dominant_page(page: Page, task: LayoutTask) -> bool:
    raster_count = _raster_region_count(task)
    return (
        raster_count is not None
        and raster_count > 0
        and len(_usable_text_elements(page)) <= 24
    )


@dataclass(frozen=True)
class RoutedIssue:
    issue_id: str
    page_index: int
    kind: str
    stage: str
    route: str
    fallback_route: str
    severity: str
    reason: str
    signals: tuple[str, ...]
    scope: dict[str, Any]
    status: str = ISSUE_STATUS_OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "page_index": self.page_index,
            "kind": self.kind,
            "stage": self.stage,
            "route": self.route,
            "fallback_route": self.fallback_route,
            "severity": self.severity,
            "reason": self.reason,
            "signals": list(self.signals),
            "scope": self.scope,
            "status": self.status,
        }


@dataclass(frozen=True)
class IssueRoutingPlan:
    source_sha256: str
    page_count: int
    issues: tuple[RoutedIssue, ...]
    contract_version: str = ISSUE_ROUTING_CONTRACT_VERSION
    mode: str = "issue-first"

    def to_dict(self) -> dict[str, Any]:
        by_page: dict[int, list[RoutedIssue]] = {
            index: [] for index in range(self.page_count)
        }
        for issue in self.issues:
            by_page[issue.page_index].append(issue)
            for related_page in issue.scope.get("related_page_indices", ()):
                by_page[related_page].append(issue)
        pages = []
        for page_index in range(self.page_count):
            page_issues = by_page[page_index]
            compatibility_route = max(
                (item.route for item in page_issues),
                key=lambda route: _ROUTE_PRIORITY[route],
                default=ROUTE_L0_RULE,
            )
            pages.append(
                {
                    "page_index": page_index,
                    "base_route": ROUTE_L0_RULE,
                    "issue_ids": [item.issue_id for item in page_issues],
                    "compatibility_route": compatibility_route,
                }
            )
        route_counts = Counter(item.route for item in self.issues)
        kind_counts = Counter(item.kind for item in self.issues)
        return {
            "contract_version": self.contract_version,
            "mode": self.mode,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "pages": pages,
            "issues": [item.to_dict() for item in self.issues],
            "summary": {
                "issue_count": len(self.issues),
                "pages_with_issues": sum(bool(items) for items in by_page.values()),
                "pages_without_issues": sum(not items for items in by_page.values()),
                "by_route": {
                    route: route_counts[route] for route in sorted(_ROUTES)
                },
                "by_kind": {
                    kind: kind_counts[kind] for kind in sorted(_ISSUE_KINDS)
                },
            },
        }

    def canonical_json(self) -> str:
        value = self.to_dict()
        validate_issue_routing(value)
        return _canonical_json(value)


def _scope(
    scope_type: str,
    *,
    bbox: Mapping[str, float] | None = None,
    candidate_ids: Sequence[str] = (),
    element_ids: Sequence[str] = (),
    related_page_indices: Sequence[int] = (),
) -> dict[str, Any]:
    return {
        "type": scope_type,
        "bbox": dict(bbox) if bbox is not None else None,
        "candidate_ids": list(candidate_ids),
        "element_ids": list(element_ids),
        "related_page_indices": list(related_page_indices),
    }


def plan_issue_routing(
    document: PhysicalDocument,
    tasks: Sequence[LayoutTask],
    *,
    risk_assessment: LayoutRiskAssessment | None = None,
) -> IssueRoutingPlan:
    """Create localized, evidence-backed escalation issues for every page."""

    pages = {page.page_index: page for page in document.pages}
    task_by_page = {task.page.page_index: task for task in tasks}
    if set(pages) != set(task_by_page):
        raise ValueError("issue routing tasks do not match document pages")
    risk_by_page = (
        {item.page_index: item for item in risk_assessment.pages}
        if risk_assessment is not None
        else {}
    )
    issues: list[RoutedIssue] = []
    ordinal_by_page: Counter[int] = Counter()

    def add(
        page_index: int,
        kind: str,
        stage: str,
        route: str,
        fallback_route: str,
        severity: str,
        reason: str,
        signals: Sequence[str],
        scope: dict[str, Any],
    ) -> None:
        ordinal_by_page[page_index] += 1
        issues.append(
            RoutedIssue(
                issue_id=(
                    f"issue-p{page_index + 1:04d}-"
                    f"{ordinal_by_page[page_index]:03d}"
                ),
                page_index=page_index,
                kind=kind,
                stage=stage,
                route=route,
                fallback_route=fallback_route,
                severity=severity,
                reason=reason,
                signals=tuple(dict.fromkeys(signals)),
                scope=scope,
            )
        )

    for page_index in sorted(pages):
        page = pages[page_index]
        task = task_by_page[page_index]
        text = _usable_text_elements(page)
        nontext_count = sum(
            item.kind in {"image", "vector"} for item in page.elements
        )
        raster_count = _raster_region_count(task)
        if not text:
            if nontext_count > 0 or (raster_count is not None and raster_count > 0):
                add(
                    page_index,
                    ISSUE_PAGE_VISUAL_PRESERVATION,
                    "projection",
                    ROUTE_L0_RULE,
                    ROUTE_HUMAN_REVIEW,
                    "required",
                    "non-empty page has no usable native text and must retain a deterministic full-page visual",
                    (
                        f"native_nontext_element_count:{nontext_count}",
                        f"raster_region_count:{raster_count or 0}",
                    ),
                    _scope("page", bbox=_task_roi(task)),
                )
            elif raster_count is None:
                add(
                    page_index,
                    ISSUE_NATIVE_TEXT_EVIDENCE_MISSING,
                    "layout",
                    ROUTE_HUMAN_REVIEW,
                    ROUTE_HUMAN_REVIEW,
                    "required",
                    "no native text and no raster blank-page evidence are available",
                    ("native_text_missing", "raster_evidence_unavailable"),
                    _scope("page", bbox=_task_roi(task)),
                )
            # raster_count == 0 is explicit evidence for a blank page.
            continue

        risk = risk_by_page.get(page_index)
        complex_geometry = (
            (risk is not None and risk.requires_full_object_analysis)
            or len(task.candidates) >= 8
            or len(task.separators) >= 12
        )
        if complex_geometry:
            signals = []
            if risk is not None:
                signals.extend(risk.reasons)
                signals.extend(risk.signals)
            signals.extend(
                (
                    f"candidate_count:{len(task.candidates)}",
                    f"separator_count:{len(task.separators)}",
                )
            )
            add(
                page_index,
                ISSUE_LAYOUT_GEOMETRY_AMBIGUITY,
                "layout",
                ROUTE_L2_VISUAL_MODEL,
                ROUTE_HUMAN_REVIEW,
                "suspicious",
                "page-local geometry cannot be resolved confidently by deterministic layout rules",
                signals,
                _scope(
                    "candidates" if task.candidates else "page",
                    bbox=_task_roi(task),
                    candidate_ids=[item.candidate_id for item in task.candidates[:64]],
                ),
            )

        # Figure presence alone is deterministic; deciding which visual it
        # describes is semantic geometry. Tables stay in the deterministic
        # same-page table renderer and may still feed back through completeness.
        captions = _caption_text_elements(page)
        if captions and (nontext_count > 0 or (raster_count or 0) > 0):
            for caption in captions[:8]:
                add(
                    page_index,
                    ISSUE_CAPTION_VISUAL_BINDING,
                    "layout",
                    ROUTE_L2_VISUAL_MODEL,
                    ROUTE_HUMAN_REVIEW,
                    "suspicious",
                    "explicit scientific caption must be bound to page-local visual evidence",
                    (
                        f"native_nontext_element_count:{nontext_count}",
                        f"raster_region_count:{raster_count or 0}",
                    ),
                    _scope(
                        "elements",
                        bbox=_normalized_element_bbox(page, caption),
                        element_ids=(caption.element_id,),
                    ),
                )

        if page_index > 0:
            previous_page = pages[page_index - 1]
            previous_task = task_by_page[page_index - 1]
            previous_visuals = _cross_page_visual_candidates(previous_task)
            current_visuals = _cross_page_visual_candidates(task)
            explicit_next_marker = _has_text_marker(
                previous_page,
                _NEXT_PAGE_CAPTION_MARKER,
            )
            previous_visual_dominant = _visual_dominant_page(
                previous_page,
                previous_task,
            )
            cross_page_captions = tuple(
                caption
                for caption in captions
                if (
                    caption.bbox.y / page.height <= 0.30
                    or not current_visuals
                )
            )
            for caption in cross_page_captions[:4]:
                explicit_previous_marker = _caption_has_previous_page_marker(
                    page,
                    caption,
                )
                has_previous_visual_evidence = bool(previous_visuals) or any(
                    (
                        explicit_next_marker,
                        explicit_previous_marker,
                        previous_visual_dominant,
                    )
                )
                if not has_previous_visual_evidence:
                    continue
                if (
                    current_visuals
                    and not explicit_next_marker
                    and not explicit_previous_marker
                    and not previous_visual_dominant
                ):
                    continue
                normalized_y = caption.bbox.y / page.height
                evidence_signals = [
                    f"visual_page_index:{page_index - 1}",
                    f"caption_page_index:{page_index}",
                    f"caption_normalized_y:{normalized_y:.6f}",
                    f"previous_page_visual_candidate_count:{len(previous_visuals)}",
                    f"current_page_visual_candidate_count:{len(current_visuals)}",
                ]
                if explicit_next_marker:
                    evidence_signals.append(
                        "previous_page_explicit_next_caption_marker"
                    )
                if explicit_previous_marker:
                    evidence_signals.append(
                        "caption_page_explicit_previous_page_marker"
                    )
                if previous_visual_dominant:
                    evidence_signals.append("previous_page_visual_dominant")
                add(
                    page_index,
                    ISSUE_CROSS_PAGE_CAPTION_VISUAL_BINDING,
                    "layout",
                    ROUTE_L2_VISUAL_MODEL,
                    ROUTE_HUMAN_REVIEW,
                    "suspicious",
                    "Figure caption on a page without local visual evidence may describe the previous page visual",
                    evidence_signals,
                    _scope(
                        "elements",
                        bbox=_normalized_element_bbox(page, caption),
                        candidate_ids=previous_visuals[:32],
                        element_ids=(caption.element_id,),
                        related_page_indices=(page_index - 1,),
                    ),
                )

    plan = IssueRoutingPlan(
        source_sha256=document.source_sha256,
        page_count=len(document.pages),
        issues=tuple(issues),
    )
    validate_issue_routing(plan.to_dict())
    return plan


def refine_issue_routing_with_text_task(
    plan_value: Mapping[str, Any],
    text_task: Mapping[str, Any],
    article_model: Mapping[str, Any],
) -> IssueRoutingPlan:
    """Discover exact L1 issues after L0 layout has produced article blocks.

    Paragraph boundaries do not exist reliably in the physical-document
    evidence used by ``layout-prepare``.  Delaying this refinement until the
    ArticleModel exists avoids guessing from raw PDF text fragments and means
    every emitted issue is already accepted by the text-review validator's
    hard preconditions.
    """

    validate_issue_routing(plan_value)
    validate_article_model(article_model)
    validate_text_task(text_task, article_model=article_model)
    source_sha256 = plan_value["source_sha256"]
    if (
        text_task["source_sha256"] != source_sha256
        or article_model["source_sha256"] != source_sha256
    ):
        raise ContractValidationError(
            "issue routing/text task/article model 文档身份不一致"
        )
    issues = [
        RoutedIssue(
            issue_id=item["issue_id"],
            page_index=item["page_index"],
            kind=item["kind"],
            stage=item["stage"],
            route=item["route"],
            fallback_route=item["fallback_route"],
            severity=item["severity"],
            reason=item["reason"],
            signals=tuple(item["signals"]),
            scope=dict(item["scope"]),
            status=item["status"],
        )
        for item in plan_value["issues"]
    ]
    existing_pairs = {
        tuple(item.scope["candidate_ids"])
        for item in issues
        if item.kind == ISSUE_PARAGRAPH_CONTINUATION
        and len(item.scope["candidate_ids"]) == 2
    }
    ordinal_by_page: Counter[int] = Counter(
        item.page_index for item in issues
    )
    model_blocks = {item["id"]: item for item in article_model["blocks"]}
    for pair_index, (previous, current) in enumerate(
        join_candidates(text_task),
        start=1,
    ):
        pair_ids = (previous["id"], current["id"])
        if pair_ids in existing_pairs:
            continue
        page_index = current["page"]
        if not 0 <= page_index < plan_value["page_count"]:
            raise ContractValidationError("text task block page 超出 issue routing")
        current_model = model_blocks.get(current["id"])
        if current_model is None:
            raise ContractValidationError("text task block 不在 article model")
        boxes = [
            span["bbox"]
            for span in current_model["source_spans"]
            if span["page_index"] == page_index
        ]
        bbox = _union_normalized_bboxes(boxes)
        ordinal_by_page[page_index] += 1
        issues.append(
            RoutedIssue(
                issue_id=(
                    f"issue-p{page_index + 1:04d}-"
                    f"{ordinal_by_page[page_index]:03d}"
                ),
                page_index=page_index,
                kind=ISSUE_PARAGRAPH_CONTINUATION,
                stage="text",
                route=ROUTE_L1_TEXT_MODEL,
                fallback_route=ROUTE_L3_PROGRAM_SYNTHESIS,
                severity="suspicious",
                reason=(
                    "validator-eligible adjacent ArticleModel blocks may be "
                    "one paragraph"
                ),
                signals=(
                    f"text_candidate_index:{pair_index}",
                    "validator_preconditions:accepted",
                ),
                scope=_scope(
                    "bbox" if bbox is not None else "page",
                    bbox=bbox,
                    candidate_ids=pair_ids,
                ),
            )
        )
        existing_pairs.add(pair_ids)
    result = IssueRoutingPlan(
        source_sha256=source_sha256,
        page_count=plan_value["page_count"],
        issues=tuple(issues),
    )
    validate_issue_routing(result.to_dict())
    return result


def refine_issue_routing(
    plan_value: Mapping[str, Any],
    completeness_report: Mapping[str, Any],
) -> IssueRoutingPlan:
    """Feed post-projection completeness findings back into local resolve."""

    validate_issue_routing(plan_value)
    validate_completeness_report(completeness_report)
    if (
        plan_value["source_sha256"] != completeness_report["source_sha256"]
        or plan_value["page_count"] != completeness_report["page_count"]
    ):
        raise ContractValidationError(
            "issue routing 与 completeness report 文档身份不一致"
        )
    issues = [
        RoutedIssue(
            issue_id=item["issue_id"],
            page_index=item["page_index"],
            kind=item["kind"],
            stage=item["stage"],
            route=item["route"],
            fallback_route=item["fallback_route"],
            severity=item["severity"],
            reason=item["reason"],
            signals=tuple(item["signals"]),
            scope=dict(item["scope"]),
            status=item["status"],
        )
        for item in plan_value["issues"]
    ]
    existing = {(item.page_index, item.kind) for item in issues}
    ordinal_by_page: Counter[int] = Counter()
    for item in issues:
        ordinal_by_page[item.page_index] += 1

    for finding in completeness_report["findings"]:
        page_index = finding["page"] - 1
        code = finding["code"]
        if code in {
            "caption_without_bound_visual",
            "vector_dense_caption_page_without_visual",
        }:
            kind = ISSUE_CAPTION_VISUAL_BINDING
            route = ROUTE_L2_VISUAL_MODEL
            fallback = ROUTE_HUMAN_REVIEW
            severity = "suspicious"
        elif code == "native_non_text_page_not_projected":
            kind = ISSUE_PAGE_VISUAL_PRESERVATION
            route = ROUTE_L0_RULE
            fallback = ROUTE_HUMAN_REVIEW
            severity = "required"
        elif code == "native_text_not_projected" or code.startswith(
            "native_text_missing_full_page_render_failed"
        ):
            kind = ISSUE_NATIVE_TEXT_EVIDENCE_MISSING
            route = ROUTE_HUMAN_REVIEW
            fallback = ROUTE_HUMAN_REVIEW
            severity = "required"
        else:
            continue
        if (page_index, kind) in existing:
            continue
        ordinal_by_page[page_index] += 1
        issues.append(
            RoutedIssue(
                issue_id=(
                    f"issue-p{page_index + 1:04d}-"
                    f"{ordinal_by_page[page_index]:03d}"
                ),
                page_index=page_index,
                kind=kind,
                stage="projection",
                route=route,
                fallback_route=fallback,
                severity=severity,
                reason=f"completeness finding requires local resolve: {code}",
                signals=(f"completeness_finding:{code}",),
                scope=_scope(
                    "page",
                    bbox={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                ),
            )
        )
        existing.add((page_index, kind))
    result = IssueRoutingPlan(
        source_sha256=plan_value["source_sha256"],
        page_count=plan_value["page_count"],
        issues=tuple(issues),
    )
    validate_issue_routing(result.to_dict())
    return result


def validate_issue_routing(value: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "mode",
        "source_sha256",
        "page_count",
        "pages",
        "issues",
        "summary",
    }
    if set(value) != required:
        raise ContractValidationError("issue routing 顶层字段非法")
    if value["contract_version"] != ISSUE_ROUTING_CONTRACT_VERSION:
        raise ContractValidationError("issue routing 契约版本非法")
    if value["mode"] != "issue-first":
        raise ContractValidationError("issue routing mode 非法")
    source = value["source_sha256"]
    if (
        not isinstance(source, str)
        or len(source) != 64
        or any(character not in "0123456789abcdef" for character in source)
    ):
        raise ContractValidationError("issue routing source hash 非法")
    page_count = value["page_count"]
    if type(page_count) is not int or page_count <= 0:
        raise ContractValidationError("issue routing page_count 非法")
    pages = value["pages"]
    issues = value["issues"]
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ContractValidationError("issue routing pages 非法")
    if not isinstance(issues, list):
        raise ContractValidationError("issue routing issues 非法")
    issue_ids: set[str] = set()
    by_page: Counter[int] = Counter()
    route_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    issue_by_id: dict[str, Mapping[str, Any]] = {}
    issue_fields = {
        "issue_id",
        "page_index",
        "kind",
        "stage",
        "route",
        "fallback_route",
        "severity",
        "reason",
        "signals",
        "scope",
        "status",
    }
    scope_fields = {
        "type",
        "bbox",
        "candidate_ids",
        "element_ids",
        "related_page_indices",
    }
    for issue in issues:
        if not isinstance(issue, Mapping) or set(issue) != issue_fields:
            raise ContractValidationError("issue routing issue 字段非法")
        issue_id = issue["issue_id"]
        page_index = issue["page_index"]
        if (
            not isinstance(issue_id, str)
            or not issue_id
            or issue_id in issue_ids
            or type(page_index) is not int
            or not 0 <= page_index < page_count
            or issue["kind"] not in _ISSUE_KINDS
            or issue["stage"] not in _STAGES
            or issue["route"] not in _ROUTES
            or issue["fallback_route"] not in _ROUTES
            or issue["severity"] not in _SEVERITIES
            or not isinstance(issue["reason"], str)
            or not issue["reason"]
            or issue["status"] != ISSUE_STATUS_OPEN
            or not isinstance(issue["signals"], list)
            or any(not isinstance(item, str) or not item for item in issue["signals"])
        ):
            raise ContractValidationError("issue routing issue 内容非法")
        scope = issue["scope"]
        if not isinstance(scope, Mapping) or set(scope) != scope_fields:
            raise ContractValidationError("issue routing scope 字段非法")
        if (
            scope["type"] not in _SCOPE_TYPES
            or not isinstance(scope["candidate_ids"], list)
            or not isinstance(scope["element_ids"], list)
            or not isinstance(scope["related_page_indices"], list)
            or len(scope["related_page_indices"])
            != len(set(scope["related_page_indices"]))
            or any(
                type(item) is not int
                or not 0 <= item < page_count
                or item == page_index
                for item in scope["related_page_indices"]
            )
            or any(
                not isinstance(item, str) or not item
                for item in scope["candidate_ids"] + scope["element_ids"]
            )
        ):
            raise ContractValidationError("issue routing scope 内容非法")
        if scope["bbox"] is not None:
            try:
                NormalizedBBox.from_dict(scope["bbox"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractValidationError("issue routing bbox 非法") from exc
        issue_ids.add(issue_id)
        issue_by_id[issue_id] = issue
        by_page[page_index] += 1
        for related_page in scope["related_page_indices"]:
            by_page[related_page] += 1
        route_counts[issue["route"]] += 1
        kind_counts[issue["kind"]] += 1

    referenced: set[str] = set()
    for expected_index, page in enumerate(pages):
        if (
            not isinstance(page, Mapping)
            or set(page)
            != {"page_index", "base_route", "issue_ids", "compatibility_route"}
            or page["page_index"] != expected_index
            or page["base_route"] != ROUTE_L0_RULE
            or page["compatibility_route"] not in _ROUTES
            or not isinstance(page["issue_ids"], list)
            or any(item not in issue_by_id for item in page["issue_ids"])
            or any(
                expected_index
                not in {
                    issue_by_id[item]["page_index"],
                    *issue_by_id[item]["scope"]["related_page_indices"],
                }
                for item in page["issue_ids"]
            )
        ):
            raise ContractValidationError("issue routing page record 非法")
        if len(page["issue_ids"]) != len(set(page["issue_ids"])):
            raise ContractValidationError("issue routing page issue 重复")
        expected_route = max(
            (issue_by_id[item]["route"] for item in page["issue_ids"]),
            key=lambda route: _ROUTE_PRIORITY[route],
            default=ROUTE_L0_RULE,
        )
        if page["compatibility_route"] != expected_route:
            raise ContractValidationError("issue routing compatibility route 不一致")
        referenced.update(page["issue_ids"])
    if referenced != issue_ids:
        raise ContractValidationError("issue routing page/issue 引用不守恒")

    summary = value["summary"]
    if (
        not isinstance(summary, Mapping)
        or set(summary)
        != {
            "issue_count",
            "pages_with_issues",
            "pages_without_issues",
            "by_route",
            "by_kind",
        }
        or summary["issue_count"] != len(issues)
        or summary["pages_with_issues"] != len(by_page)
        or summary["pages_without_issues"] != page_count - len(by_page)
        or summary["by_route"]
        != {route: route_counts[route] for route in sorted(_ROUTES)}
        or summary["by_kind"]
        != {kind: kind_counts[kind] for kind in sorted(_ISSUE_KINDS)}
    ):
        raise ContractValidationError("issue routing summary 不一致")


def canonical_issue_routing_json(value: Mapping[str, Any]) -> str:
    validate_issue_routing(value)
    return _canonical_json(value)


__all__ = [
    "ISSUE_CAPTION_VISUAL_BINDING",
    "ISSUE_LAYOUT_GEOMETRY_AMBIGUITY",
    "ISSUE_NATIVE_TEXT_EVIDENCE_MISSING",
    "ISSUE_PAGE_VISUAL_PRESERVATION",
    "ISSUE_PARAGRAPH_CONTINUATION",
    "ISSUE_ROUTING_CONTRACT_VERSION",
    "IssueRoutingPlan",
    "RoutedIssue",
    "canonical_issue_routing_json",
    "plan_issue_routing",
    "refine_issue_routing",
    "refine_issue_routing_with_text_task",
    "validate_issue_routing",
]
