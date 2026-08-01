"""Deterministic confidence gates for selective layout extraction upgrades."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .layout_models import LayoutTask
from .models import PhysicalDocument


@dataclass(frozen=True)
class LayoutRiskPolicy:
    """Versioned, deterministic gates for page-local extraction upgrades."""

    candidate_count_hard: int = 32
    raster_candidate_count_hard: int = 8
    separator_count_hard: int = 80
    candidate_count_elevated: int = 16
    raster_candidate_count_elevated: int = 4
    separator_count_elevated: int = 40
    separator_density_elevated: float = 3.0
    mixed_candidate_count_elevated: int = 3
    boundary_crossing_count_elevated: int = 2
    raster_suppressed_count_elevated: int = 6
    peripheral_ratio_elevated: float = 0.4
    composite_score_threshold: int = 3
    policy_version: str = "paper2md-layout-risk-v0.2"

    def to_dict(self) -> dict[str, int | float | str]:
        return {
            "policy_version": self.policy_version,
            "candidate_count_hard": self.candidate_count_hard,
            "raster_candidate_count_hard": self.raster_candidate_count_hard,
            "separator_count_hard": self.separator_count_hard,
            "candidate_count_elevated": self.candidate_count_elevated,
            "raster_candidate_count_elevated": (
                self.raster_candidate_count_elevated
            ),
            "separator_count_elevated": self.separator_count_elevated,
            "separator_density_elevated": self.separator_density_elevated,
            "mixed_candidate_count_elevated": (
                self.mixed_candidate_count_elevated
            ),
            "boundary_crossing_count_elevated": (
                self.boundary_crossing_count_elevated
            ),
            "raster_suppressed_count_elevated": (
                self.raster_suppressed_count_elevated
            ),
            "peripheral_ratio_elevated": self.peripheral_ratio_elevated,
            "composite_score_threshold": self.composite_score_threshold,
        }


@dataclass(frozen=True)
class PageLayoutRisk:
    page_index: int
    reasons: tuple[str, ...]
    candidate_count: int
    raster_candidate_count: int
    separator_count: int
    native_text_element_count: int
    risk_score: int = 0
    signals: tuple[str, ...] = ()
    metrics: dict[str, int | float] = field(default_factory=dict)

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
            "risk_score": self.risk_score,
            "signals": list(self.signals),
            "metrics": dict(sorted(self.metrics.items())),
        }


@dataclass(frozen=True)
class LayoutRiskAssessment:
    pages: tuple[PageLayoutRisk, ...]
    policy_version: str = "paper2md-layout-risk-v0.2"
    policy: dict[str, int | float | str] = field(default_factory=dict)

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
            "policy": dict(sorted(self.policy.items())),
            "escalation_scope": "page",
            "escalation_page_indices": list(self.escalation_page_indices),
            "pages": [page.to_dict() for page in self.pages],
        }


def assess_layout_risk(
    tasks: Sequence[LayoutTask],
    document: PhysicalDocument,
    *,
    policy: LayoutRiskPolicy | None = None,
) -> LayoutRiskAssessment:
    """Return explainable page-local reasons for a full object walk.

    Missing evidence and extreme counts remain hard gates. Moderate signals
    only escalate when several independent dimensions agree, which avoids
    treating one arbitrary count as proof of semantic correctness.
    """

    settings = policy or LayoutRiskPolicy()
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
        mixed_count = sum(
            len(candidate.element_kinds) > 1
            for candidate in task.candidates
        )
        peripheral_count = sum(
            candidate.features.get("peripheral_hint") is True
            for candidate in task.candidates
        )
        boundary_count = len(
            task.metadata.get("boundary_crossing_element_ids", ())
        )
        suppressed_count = len(
            task.metadata.get("raster_suppressed_element_ids", ())
        )
        candidate_count = len(task.candidates)
        separator_count = len(task.separators)
        separator_density = separator_count / max(candidate_count, 1)
        peripheral_ratio = peripheral_count / max(candidate_count, 1)
        reasons: list[str] = []
        signals: list[str] = []
        risk_score = 0
        if text_count == 0:
            reasons.append("native_text_missing")
        if not task.candidates:
            reasons.append("no_layout_candidates")
        if candidate_count > settings.candidate_count_hard:
            reasons.append("candidate_fragmentation_high")
        elif candidate_count >= settings.candidate_count_elevated:
            signals.append("candidate_fragmentation_elevated")
            risk_score += 1
        if raster_count > settings.raster_candidate_count_hard:
            reasons.append("raster_region_ambiguity_high")
        elif raster_count >= settings.raster_candidate_count_elevated:
            signals.append("raster_region_ambiguity_elevated")
            risk_score += 1
        separator_risk = False
        if separator_count > settings.separator_count_hard:
            reasons.append("separator_ambiguity_high")
        elif separator_count >= settings.separator_count_elevated:
            signals.append("separator_ambiguity_elevated")
            separator_risk = True
        if (
            separator_count >= 12
            and separator_density >= settings.separator_density_elevated
        ):
            signals.append("separator_density_elevated")
            separator_risk = True
        if separator_risk:
            risk_score += 1
        if mixed_count >= settings.mixed_candidate_count_elevated:
            signals.append("mixed_content_ambiguity_elevated")
            risk_score += 1
        if boundary_count >= settings.boundary_crossing_count_elevated:
            signals.append("roi_boundary_ambiguity_elevated")
            risk_score += 1
        if suppressed_count >= settings.raster_suppressed_count_elevated:
            signals.append("raster_text_overlap_elevated")
            risk_score += 1
        if (
            candidate_count >= 5
            and peripheral_ratio >= settings.peripheral_ratio_elevated
        ):
            signals.append("peripheral_content_ambiguity_elevated")
            risk_score += 1
        if not reasons and risk_score >= settings.composite_score_threshold:
            reasons.append("combined_layout_ambiguity")
        results.append(
            PageLayoutRisk(
                page_index=page.page_index,
                reasons=tuple(reasons),
                candidate_count=candidate_count,
                raster_candidate_count=raster_count,
                separator_count=separator_count,
                native_text_element_count=text_count,
                risk_score=risk_score,
                signals=tuple(signals),
                metrics={
                    "boundary_crossing_element_count": boundary_count,
                    "mixed_candidate_count": mixed_count,
                    "peripheral_candidate_count": peripheral_count,
                    "peripheral_candidate_ratio": round(peripheral_ratio, 6),
                    "raster_suppressed_element_count": suppressed_count,
                    "separator_density": round(separator_density, 6),
                },
            )
        )
    return LayoutRiskAssessment(
        tuple(results),
        policy_version=settings.policy_version,
        policy=settings.to_dict(),
    )
