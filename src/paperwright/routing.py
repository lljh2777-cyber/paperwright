"""Deterministic page-level routing between rule, text, visual and program layers.

The router never calls a model.  It turns physical evidence, layout risk and
rule signals into advisory decisions that Agent bridges can execute; every
model-produced artifact still has to pass PaperWright validators.

Contract: ``paperwright-routing-v0.1``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .layout_models import LayoutTask
from .layout_risk import LayoutRiskAssessment
from .models import PhysicalDocument

ROUTING_CONTRACT_VERSION = "paperwright-routing-v0.1"
ROUTE_L0_RULE = "L0_RULE"
ROUTE_L1_TEXT_MODEL = "L1_TEXT_MODEL"
ROUTE_L2_VISUAL_MODEL = "L2_VISUAL_MODEL"
ROUTE_L3_PROGRAM_SYNTHESIS = "L3_PROGRAM_SYNTHESIS"
ROUTE_HUMAN_REVIEW = "HUMAN_REVIEW"

_ROUTES = {
    ROUTE_L0_RULE,
    ROUTE_L1_TEXT_MODEL,
    ROUTE_L2_VISUAL_MODEL,
    ROUTE_L3_PROGRAM_SYNTHESIS,
    ROUTE_HUMAN_REVIEW,
}

_CAPTION_LIKE = ("figure", "fig", "table")


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


@dataclass(frozen=True)
class PageRoute:
    page_index: int
    route: str
    reasons: tuple[str, ...]
    signals: tuple[str, ...]
    fallback_route: str
    actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "route": self.route,
            "reasons": list(self.reasons),
            "signals": list(self.signals),
            "fallback_route": self.fallback_route,
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class RoutingPlan:
    source_sha256: str
    pages: tuple[PageRoute, ...]
    contract_version: str = ROUTING_CONTRACT_VERSION
    mode: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "mode": self.mode,
            "source_sha256": self.source_sha256,
            "page_count": len(self.pages),
            "pages": [page.to_dict() for page in self.pages],
            "summary": {
                route: sum(page.route == route for page in self.pages)
                for route in sorted(_ROUTES)
            },
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())


def _route_actions(route: str, page_index: int) -> tuple[str, ...]:
    page = f"page-{page_index + 1:04d}"
    if route == ROUTE_L0_RULE:
        return ()
    if route == ROUTE_L1_TEXT_MODEL:
        return (
            "paperwright text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON",
            f"PYTHONPATH=src python tools/run_text_review.py TEXT_TASK_JSON TEXT_REVIEW_JSON",
        )
    if route == ROUTE_L2_VISUAL_MODEL:
        return (
            f"paperwright layout-prepare INPUT_PDF REVIEW_DIR --review-mode visual-direct",
            f"PYTHONPATH=src python tools/run_visual_review.py REVIEW_DIR --pages {page_index + 1}",
        )
    if route == ROUTE_L3_PROGRAM_SYNTHESIS:
        return (
            "paperwright text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON",
            "PYTHONPATH=src python tools/run_text_synthesize.py ARTICLE_MODEL_JSON TEXT_TASK_JSON TEXT_REVIEW_JSON",
        )
    return (
        "human review required: confirm ROI and final layout manually",
    )


def _lower_fragment_count(page) -> int:
    count = 0
    for element in page.elements:
        text = (element.text or "").lstrip()
        if not text or len(text) > 40:
            continue
        first_word = text.split()[0] if text.split() else ""
        if first_word and first_word[0].islower():
            count += 1
    return count


def _caption_like_text_count(page) -> int:
    return sum(
        bool((element.text or "").casefold().split(".")[0] in _CAPTION_LIKE)
        for element in page.elements
        if element.kind == "text" and element.text
    )


def plan_routing(
    document: PhysicalDocument,
    tasks: Sequence[LayoutTask],
    *,
    risk_assessment: LayoutRiskAssessment | None = None,
    mode: str = "auto",
) -> RoutingPlan:
    """Produce one advisory route per page from deterministic evidence only."""

    if mode not in {"auto", "off"}:
        raise ValueError("routing mode must be auto or off")
    pages_by_index = {page.page_index: page for page in document.pages}
    task_by_page = {task.page.page_index: task for task in tasks}
    risk_by_page = (
        {
            item.page_index: item
            for item in risk_assessment.pages
        }
        if risk_assessment is not None
        else {}
    )
    if set(task_by_page) != set(pages_by_index):
        raise ValueError("routing tasks do not match document pages")

    decisions: list[PageRoute] = []
    for page_index in sorted(pages_by_index):
        page = pages_by_index[page_index]
        task = task_by_page[page_index]
        risk = risk_by_page.get(page_index)
        text_count = sum(element.kind == "text" for element in page.elements)
        candidate_count = len(task.candidates)
        separator_count = len(task.separators)
        lower_fragments = _lower_fragment_count(page)
        caption_like = _caption_like_text_count(page)
        signals: list[str] = []
        reasons: list[str] = []

        if mode == "off":
            route = ROUTE_L0_RULE
            reasons.append("routing_disabled")
        elif text_count == 0:
            route = ROUTE_HUMAN_REVIEW
            reasons.append("native_text_missing")
            signals.append("no_native_text_layer")
        elif risk is not None and risk.requires_full_object_analysis:
            route = ROUTE_L2_VISUAL_MODEL
            reasons.append("layout_risk_escalation")
            signals.extend(risk.reasons[:4])
        elif candidate_count >= 8 or separator_count >= 20:
            route = ROUTE_L2_VISUAL_MODEL
            reasons.append("complex_layout_geometry")
            signals.extend(
                [
                    f"candidate_count:{candidate_count}",
                    f"separator_count:{separator_count}",
                ]
            )
        elif separator_count >= 12 and candidate_count >= 4:
            route = ROUTE_L2_VISUAL_MODEL
            reasons.append("moderate_layout_with_visual_ambiguity")
            signals.append(f"separator_count:{separator_count}")
        elif lower_fragments >= 2 and text_count >= 3:
            route = ROUTE_L1_TEXT_MODEL
            reasons.append("lowercase_continuation_fragments")
            signals.append(f"lower_fragment_count:{lower_fragments}")
        else:
            route = ROUTE_L0_RULE
            reasons.append("ordinary_native_text_layout")

        fallback = ROUTE_L0_RULE
        if route == ROUTE_L2_VISUAL_MODEL:
            fallback = ROUTE_HUMAN_REVIEW
        elif route == ROUTE_L1_TEXT_MODEL:
            fallback = ROUTE_L0_RULE
        elif route == ROUTE_HUMAN_REVIEW:
            fallback = ROUTE_L0_RULE

        if caption_like:
            signals.append(f"caption_like_lines:{caption_like}")

        decisions.append(
            PageRoute(
                page_index=page_index,
                route=route,
                reasons=tuple(dict.fromkeys(reasons)),
                signals=tuple(dict.fromkeys(signals)),
                fallback_route=fallback,
                actions=_route_actions(route, page_index),
            )
        )
    return RoutingPlan(
        source_sha256=document.source_sha256,
        pages=tuple(decisions),
        mode=mode,
    )


def canonical_routing_json(value: Mapping[str, Any]) -> str:
    return _canonical_json(value)


__all__ = [
    "PageRoute",
    "ROUTING_CONTRACT_VERSION",
    "ROUTE_HUMAN_REVIEW",
    "ROUTE_L0_RULE",
    "ROUTE_L1_TEXT_MODEL",
    "ROUTE_L2_VISUAL_MODEL",
    "ROUTE_L3_PROGRAM_SYNTHESIS",
    "RoutingPlan",
    "canonical_routing_json",
    "plan_routing",
]
