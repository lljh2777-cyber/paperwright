"""Deterministic confidence gates for selective layout extraction upgrades."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .layout_models import LayoutTask
from .models import PhysicalDocument


@dataclass(frozen=True)
class PageLayoutRisk:
    page_index: int
    reasons: tuple[str, ...]
    candidate_count: int
    raster_candidate_count: int
    separator_count: int
    native_text_element_count: int

    @property
    def requires_full_object_analysis(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "page_index": self.page_index,
            "requires_full_object_analysis": self.requires_full_object_analysis,
            "reasons": list(self.reasons),
            "candidate_count": self.candidate_count,
            "raster_candidate_count": self.raster_candidate_count,
            "separator_count": self.separator_count,
            "native_text_element_count": self.native_text_element_count,
        }


@dataclass(frozen=True)
class LayoutRiskAssessment:
    pages: tuple[PageLayoutRisk, ...]
    policy_version: str = "paper2md-layout-risk-v0.1"

    @property
    def escalation_page_indices(self) -> tuple[int, ...]:
        return tuple(
            page.page_index
            for page in self.pages
            if page.requires_full_object_analysis
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "escalation_scope": "page",
            "escalation_page_indices": list(self.escalation_page_indices),
            "pages": [page.to_dict() for page in self.pages],
        }


def assess_layout_risk(
    tasks: Sequence[LayoutTask],
    document: PhysicalDocument,
) -> LayoutRiskAssessment:
    """Return page-local reasons for a selective full object walk."""

    pages_by_index = {page.page_index: page for page in document.pages}
    if {task.page.page_index for task in tasks} != set(pages_by_index):
        raise ValueError("layout risk tasks do not match document pages")
    results: list[PageLayoutRisk] = []
    for task in sorted(tasks, key=lambda item: item.page.page_index):
        page = pages_by_index[task.page.page_index]
        text_count = sum(item.kind == "text" for item in page.elements)
        raster_count = sum(
            "raster" in candidate.element_kinds
            for candidate in task.candidates
        )
        reasons: list[str] = []
        if text_count == 0:
            reasons.append("native_text_missing")
        if not task.candidates:
            reasons.append("no_layout_candidates")
        if len(task.candidates) > 32:
            reasons.append("candidate_fragmentation_high")
        if raster_count > 8:
            reasons.append("raster_region_ambiguity_high")
        if len(task.separators) > 80:
            reasons.append("separator_ambiguity_high")
        results.append(
            PageLayoutRisk(
                page_index=page.page_index,
                reasons=tuple(reasons),
                candidate_count=len(task.candidates),
                raster_candidate_count=raster_count,
                separator_count=len(task.separators),
                native_text_element_count=text_count,
            )
        )
    return LayoutRiskAssessment(tuple(results))
