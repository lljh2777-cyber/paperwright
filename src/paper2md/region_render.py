"""Conservative, deterministic planning for clipped page-region rendering.

This module never renders a PDF.  It derives a page-local request from native
image, vector, text, and explicit caption evidence.  The backend remains
responsible for rendering and for enforcing the pixel-level safety checks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .figures import CaptionCandidate, FigureAnalysis, FigureGroup
from .models import BBox, Element, Page, PhysicalDocument

REGION_RENDER_SCALE = 2.0
REGION_RENDER_DPI = 144.0
REGION_RENDER_MAX_PIXELS = 16_000_000
REGION_RENDER_MAX_PAGE_AREA_RATIO = 0.82
REGION_RENDER_MIN_VARIANCE = 2.0
REGION_RENDER_CAPTION_GUARD = 4.0
_MIN_VECTOR_EVIDENCE = 4
_FIGURE_TEXT_PADDING = 4.0
_FIGURE_TEXT_TOP_LOOKBACK = 12.0
_MIN_PAGE_AREA_RATIO = 0.01
_MIN_CAPTION_CONFIDENCE = 0.85
_MIN_VECTOR_EXTENSION = 2.0
_LONG_TEXT_CHARACTERS = 100
_WIDE_TEXT_PAGE_RATIO = 0.25
_HEADER_FOOTER_GUARD_RATIO = 0.04
_MAX_CAPTION_HORIZONTAL_EXCLUSION_RATIO = 0.15
_CONTINUED = re.compile(r"\bcontinued\s+on\s+next\s+page\b", re.IGNORECASE)


@dataclass(frozen=True)
class RegionRenderRequest:
    figure_id: str
    page_index: int
    bbox: BBox
    caption_top: float
    caption_id: str
    caption_element_ids: tuple[str, ...]
    caption_text: str
    caption_bbox: BBox
    caption_reason: str
    caption_confidence: float
    member_element_ids: tuple[str, ...]
    vector_evidence_element_ids: tuple[str, ...]
    vector_evidence_count: int
    vector_evidence_sha256: str
    fallback_reason: str
    bbox_rule: str
    scale: float = REGION_RENDER_SCALE
    dpi: float = REGION_RENDER_DPI
    max_pixels: int = REGION_RENDER_MAX_PIXELS
    max_page_area_ratio: float = REGION_RENDER_MAX_PAGE_AREA_RATIO
    min_variance: float = REGION_RENDER_MIN_VARIANCE
    caption_guard: float = REGION_RENDER_CAPTION_GUARD


@dataclass(frozen=True)
class RegionRenderResult:
    figure_id: str
    data: bytes
    width_px: int
    height_px: int
    sha256: str
    pixel_variance: float
    page_area_ratio: float
    page_rotation: int
    renderer_version: str
    source_sha256: str
    bbox: BBox
    scale: float
    dpi: float


@dataclass(frozen=True)
class RegionRenderDecision:
    status: str
    page_index: int
    figure_id: str | None
    reason: str
    evidence_element_ids: tuple[str, ...]
    request: RegionRenderRequest | None = None


def _contains(outer: BBox, inner: BBox, tolerance: float = 0.5) -> bool:
    return (
        outer.x <= inner.x + tolerance
        and outer.y <= inner.y + tolerance
        and outer.right + tolerance >= inner.right
        and outer.bottom + tolerance >= inner.bottom
    )


def _union(boxes: list[BBox]) -> BBox:
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return BBox(left, top, right - left, bottom - top)


def _expanded(box: BBox, page: Page, padding: float) -> BBox:
    left = max(0.0, box.x - padding)
    top = max(0.0, box.y - padding)
    right = min(page.width, box.right + padding)
    bottom = min(page.height, box.bottom + padding)
    return BBox(left, top, right - left, bottom - top)


def _continuation_elements(page: Page) -> tuple[Element, ...]:
    return tuple(
        item
        for item in page.elements
        if item.kind == "text" and item.text and _CONTINUED.search(item.text)
    )


def _candidate_bbox(
    page: Page,
    group: FigureGroup,
    caption: CaptionCandidate,
) -> tuple[BBox, str, tuple[str, ...], int] | None:
    caption_limit = caption.bbox.y - REGION_RENDER_CAPTION_GUARD
    vectors = [
        item
        for item in page.elements
        if item.kind == "vector" and item.bbox.bottom <= caption_limit
    ]
    containing = [
        item
        for item in vectors
        if _contains(item.bbox, group.bbox)
        and item.bbox.width * item.bbox.height
        < page.width * page.height * REGION_RENDER_MAX_PAGE_AREA_RATIO
    ]
    if containing:
        frame = min(
            containing,
            key=lambda item: (
                item.bbox.width * item.bbox.height,
                item.element_id,
            ),
        )
        inside_vectors = [
            item for item in vectors if _contains(frame.bbox, item.bbox)
        ]
        if len(inside_vectors) >= _MIN_VECTOR_EVIDENCE:
            return (
                frame.bbox,
                "smallest_same_page_native_vector_frame_containing_bitmap_group",
                tuple(sorted(item.element_id for item in inside_vectors)),
                len(inside_vectors),
            )

    horizontal_left = group.bbox.x - _FIGURE_TEXT_PADDING
    horizontal_right = group.bbox.right + _FIGURE_TEXT_PADDING
    top_limit = max(0.0, group.bbox.y - _FIGURE_TEXT_TOP_LOOKBACK)
    evidence: list[Element] = []
    caption_ids = set(caption.element_ids)
    for item in page.elements:
        if item.element_id in caption_ids:
            continue
        center_x = item.bbox.x + item.bbox.width / 2
        center_y = item.bbox.y + item.bbox.height / 2
        if (
            horizontal_left <= center_x <= horizontal_right
            and top_limit <= center_y < caption.bbox.y
            and item.kind in {"text", "image", "vector"}
        ):
            evidence.append(item)
    vector_evidence = [item for item in evidence if item.kind == "vector"]
    if len(vector_evidence) < _MIN_VECTOR_EVIDENCE:
        return None
    bbox = _expanded(_union([item.bbox for item in evidence]), page, _FIGURE_TEXT_PADDING)
    if bbox.bottom > caption_limit:
        bbox = BBox(bbox.x, bbox.y, bbox.width, caption_limit - bbox.y)
    return (
        bbox,
        "same_page_image_vector_text_union_between_header_and_explicit_caption",
        tuple(sorted(item.element_id for item in vector_evidence)),
        len(vector_evidence),
    )


def _plan_explicit_renders(
    document: PhysicalDocument,
    analysis: FigureAnalysis,
) -> tuple[RegionRenderDecision, ...]:
    """Preserve the bounded spike planner for explicit page debugging."""

    decisions: list[RegionRenderDecision] = []
    groups_by_page: dict[int, list[FigureGroup]] = {}
    for group in analysis.groups:
        groups_by_page.setdefault(group.page_index, []).append(group)

    for page in document.pages:
        continued = _continuation_elements(page)
        if continued and (
            any(item.kind == "image" for item in page.elements)
            and any(item.kind == "vector" for item in page.elements)
        ):
            decisions.append(
                RegionRenderDecision(
                    status="rejected",
                    page_index=page.page_index,
                    figure_id=None,
                    reason="cross_page_figure_continuation_explicitly_detected",
                    evidence_element_ids=tuple(
                        sorted(item.element_id for item in continued)
                    ),
                )
            )
            continue
        claimed_captions = {
            group.caption.caption_id
            for group in groups_by_page.get(page.page_index, [])
            if group.caption_status == "matched" and group.caption is not None
        }
        page_captions = [
            item
            for item in analysis.caption_candidates
            if item.page_index == page.page_index
        ]
        for group in groups_by_page.get(page.page_index, []):
            caption = group.caption
            caption_reason = group.caption_reason
            caption_confidence = group.caption_confidence or 0.0
            if caption is None:
                possible = [
                    item
                    for item in page_captions
                    if item.caption_id not in claimed_captions
                    and item.bbox.y > group.bbox.bottom
                    and item.bbox.y - group.bbox.bottom <= 200.0
                    and not (
                        item.bbox.right < group.bbox.x
                        or item.bbox.x > group.bbox.right
                    )
                ]
                if len(possible) != 1:
                    continue
                caption = possible[0]
                caption_reason = (
                    "unique_same_page_explicit_caption_with_vector_bridge"
                )
                caption_confidence = 0.85
            candidate = _candidate_bbox(page, group, caption)
            if candidate is None:
                continue
            bbox, rule, vector_ids, vector_count = candidate
            page_ratio = bbox.width * bbox.height / (page.width * page.height)
            if page_ratio >= REGION_RENDER_MAX_PAGE_AREA_RATIO:
                decisions.append(
                    RegionRenderDecision(
                        status="rejected",
                        page_index=page.page_index,
                        figure_id=group.figure_id,
                        reason="near_full_page_region_rejected",
                        evidence_element_ids=vector_ids[:128],
                    )
                )
                continue
            request = RegionRenderRequest(
                figure_id=group.figure_id,
                page_index=page.page_index,
                bbox=bbox,
                caption_top=caption.bbox.y,
                caption_id=caption.caption_id,
                caption_element_ids=caption.element_ids,
                caption_text=caption.text,
                caption_bbox=caption.bbox,
                caption_reason=caption_reason,
                caption_confidence=caption_confidence,
                member_element_ids=group.member_element_ids,
                vector_evidence_element_ids=vector_ids[:128],
                vector_evidence_count=vector_count,
                vector_evidence_sha256=hashlib.sha256(
                    "\n".join(vector_ids).encode("utf-8")
                ).hexdigest(),
                fallback_reason=(
                    "native_bitmap_asset_is_incomplete_for_mixed_bitmap_vector_figure"
                ),
                bbox_rule=rule,
            )
            decisions.append(
                RegionRenderDecision(
                    status="requested",
                    page_index=page.page_index,
                    figure_id=group.figure_id,
                    reason=request.fallback_reason,
                    evidence_element_ids=vector_ids[:128],
                    request=request,
                )
            )
    return tuple(decisions)


def _candidate_has_missing_native_content(
    page: Page,
    group: FigureGroup,
    bbox: BBox,
    vector_ids: tuple[str, ...],
    rule: str,
) -> bool:
    """Require evidence that a native bitmap/group is visually incomplete."""

    by_id = {item.element_id: item for item in page.elements}
    vectors = [by_id[item] for item in vector_ids if item in by_id]
    extends = any(
        item.bbox.x < group.bbox.x - _MIN_VECTOR_EXTENSION
        or item.bbox.y < group.bbox.y - _MIN_VECTOR_EXTENSION
        or item.bbox.right > group.bbox.right + _MIN_VECTOR_EXTENSION
        or item.bbox.bottom > group.bbox.bottom + _MIN_VECTOR_EXTENSION
        for item in vectors
    )
    boundary_expands = (
        bbox.x < group.bbox.x - _MIN_VECTOR_EXTENSION
        or bbox.y < group.bbox.y - _MIN_VECTOR_EXTENSION
        or bbox.right > group.bbox.right + _MIN_VECTOR_EXTENSION
        or bbox.bottom > group.bbox.bottom + _MIN_VECTOR_EXTENSION
    )
    structurally_bridged_group = (
        group.extraction_mode == "grouped"
        and len(group.member_element_ids) > 1
        and len(vector_ids) >= _MIN_VECTOR_EVIDENCE
    )
    framed_overlay = (
        rule == "smallest_same_page_native_vector_frame_containing_bitmap_group"
        and len(vector_ids) >= _MIN_VECTOR_EVIDENCE
        and boundary_expands
    )
    return extends or structurally_bridged_group or framed_overlay


def _intrusion_reason(
    page: Page,
    bbox: BBox,
    caption: CaptionCandidate,
) -> tuple[str | None, tuple[str, ...]]:
    if bbox.y < page.height * _HEADER_FOOTER_GUARD_RATIO:
        return "suspected_header_or_footer_intrusion", ()
    if bbox.bottom > page.height * (1.0 - _HEADER_FOOTER_GUARD_RATIO):
        return "suspected_header_or_footer_intrusion", ()
    caption_ids = set(caption.element_ids)
    intrusions = []
    for item in page.elements:
        if (
            item.kind != "text"
            or not item.text
            or item.element_id in caption_ids
        ):
            continue
        center_x = item.bbox.x + item.bbox.width / 2
        center_y = item.bbox.y + item.bbox.height / 2
        if not (
            bbox.x <= center_x <= bbox.right
            and bbox.y <= center_y <= bbox.bottom
        ):
            continue
        normalized = re.sub(r"\s+", " ", item.text).strip()
        if (
            len(normalized) >= _LONG_TEXT_CHARACTERS
            and item.bbox.width / page.width >= _WIDE_TEXT_PAGE_RATIO
        ):
            intrusions.append(item.element_id)
    if intrusions:
        return "suspected_body_text_intrusion", tuple(sorted(intrusions))
    return None, ()


def _auto_caption(
    group: FigureGroup,
    page_captions: list[CaptionCandidate],
    claimed_captions: set[str],
) -> tuple[CaptionCandidate | None, str, float, str | None]:
    if group.caption_status == "ambiguous":
        return None, group.caption_reason, 0.0, "caption_ambiguous"
    if group.caption is not None:
        confidence = group.caption_confidence or 0.0
        if confidence < _MIN_CAPTION_CONFIDENCE:
            return (
                None,
                group.caption_reason,
                confidence,
                "caption_confidence_below_threshold",
            )
        return group.caption, group.caption_reason, confidence, None
    possible = [
        item
        for item in page_captions
        if item.caption_id not in claimed_captions
        and item.bbox.y > group.bbox.bottom
        and item.bbox.y - group.bbox.bottom <= 200.0
        and not (
            item.bbox.right < group.bbox.x
            or item.bbox.x > group.bbox.right
        )
    ]
    if not possible:
        return None, group.caption_reason, 0.0, "caption_missing_or_unmatched"
    if len(possible) != 1:
        return None, group.caption_reason, 0.0, "caption_ambiguous"
    return (
        possible[0],
        "unique_same_page_explicit_caption_with_vector_bridge",
        0.85,
        None,
    )


def _auto_decision_for_group(
    page: Page,
    group: FigureGroup,
    page_captions: list[CaptionCandidate],
    claimed_captions: set[str],
) -> RegionRenderDecision | None:
    caption, caption_reason, confidence, rejection = _auto_caption(
        group, page_captions, claimed_captions
    )
    if rejection is not None or caption is None:
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason=rejection or "caption_missing_or_unmatched",
            evidence_element_ids=group.vector_evidence_element_ids,
        )
    candidate = _candidate_bbox(page, group, caption)
    if candidate is None:
        # A complete bitmap with no surrounding vector boundary is normal,
        # not a degraded auto-render failure.
        if not any(item.kind == "vector" for item in page.elements):
            return None
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="insufficient_deterministic_region_boundary",
            evidence_element_ids=group.vector_evidence_element_ids,
        )
    bbox, rule, vector_ids, vector_count = candidate
    if (
        bbox.width <= 0
        or bbox.height <= 0
        or bbox.x < 0
        or bbox.y < 0
        or bbox.right > page.width + 1e-6
        or bbox.bottom > page.height + 1e-6
    ):
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="bbox_out_of_bounds_or_nonpositive",
            evidence_element_ids=vector_ids[:128],
        )
    page_ratio = bbox.width * bbox.height / (page.width * page.height)
    if page_ratio >= REGION_RENDER_MAX_PAGE_AREA_RATIO:
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="near_full_page_region_rejected",
            evidence_element_ids=vector_ids[:128],
        )
    if page_ratio < _MIN_PAGE_AREA_RATIO:
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="region_below_minimum_figure_area",
            evidence_element_ids=vector_ids[:128],
        )
    if bbox.bottom > caption.bbox.y - REGION_RENDER_CAPTION_GUARD + 1e-6:
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="caption_guard_intrusion",
            evidence_element_ids=vector_ids[:128],
        )
    caption_excluded_width = max(0.0, bbox.x - caption.bbox.x) + max(
        0.0, caption.bbox.right - bbox.right
    )
    if (
        caption.bbox.width > 0
        and caption_excluded_width / caption.bbox.width
        > _MAX_CAPTION_HORIZONTAL_EXCLUSION_RATIO
    ):
        # A substantially wider explicit caption is conservative evidence that
        # the candidate boundary captured only part of a multi-panel figure.
        # Rejecting preserves the complete native asset and avoids approving a
        # visibly clipped region.
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason="candidate_does_not_cover_caption_horizontal_span",
            evidence_element_ids=tuple(
                sorted(set(vector_ids[:128]) | set(caption.element_ids))
            ),
        )
    intrusion, intrusion_ids = _intrusion_reason(page, bbox, caption)
    if intrusion is not None:
        return RegionRenderDecision(
            status="rejected",
            page_index=page.page_index,
            figure_id=group.figure_id,
            reason=intrusion,
            evidence_element_ids=tuple(
                sorted(set(vector_ids[:128]) | set(intrusion_ids))
            ),
        )
    if not _candidate_has_missing_native_content(
        page, group, bbox, vector_ids, rule
    ):
        return None
    request = RegionRenderRequest(
        figure_id=group.figure_id,
        page_index=page.page_index,
        bbox=bbox,
        caption_top=caption.bbox.y,
        caption_id=caption.caption_id,
        caption_element_ids=caption.element_ids,
        caption_text=caption.text,
        caption_bbox=caption.bbox,
        caption_reason=caption_reason,
        caption_confidence=confidence,
        member_element_ids=group.member_element_ids,
        vector_evidence_element_ids=vector_ids[:128],
        vector_evidence_count=vector_count,
        vector_evidence_sha256=hashlib.sha256(
            "\n".join(vector_ids).encode("utf-8")
        ).hexdigest(),
        fallback_reason=(
            "native_bitmap_asset_is_incomplete_for_mixed_bitmap_vector_figure"
        ),
        bbox_rule=rule,
    )
    return RegionRenderDecision(
        status="requested",
        page_index=page.page_index,
        figure_id=group.figure_id,
        reason=request.fallback_reason,
        evidence_element_ids=vector_ids[:128],
        request=request,
    )


def _plan_auto_renders(
    document: PhysicalDocument,
    analysis: FigureAnalysis,
    *,
    max_candidates: int,
) -> tuple[RegionRenderDecision, ...]:
    decisions: list[RegionRenderDecision] = []
    groups_by_page: dict[int, list[FigureGroup]] = {}
    for group in analysis.groups:
        groups_by_page.setdefault(group.page_index, []).append(group)

    for page in document.pages:
        page_groups = sorted(
            groups_by_page.get(page.page_index, []),
            key=lambda item: (item.bbox.y, item.bbox.x, item.figure_id),
        )
        page_captions = [
            item
            for item in analysis.caption_candidates
            if item.page_index == page.page_index
        ]
        continued = _continuation_elements(page)
        if continued and (
            any(item.kind == "image" for item in page.elements)
            and any(item.kind == "vector" for item in page.elements)
        ):
            decisions.append(
                RegionRenderDecision(
                    status="rejected",
                    page_index=page.page_index,
                    figure_id=None,
                    reason="cross_page_figure_continuation_explicitly_detected",
                    evidence_element_ids=tuple(
                        sorted(item.element_id for item in continued)
                    ),
                )
            )
            continue

        caption_owners: dict[str, list[str]] = {}
        for group in page_groups:
            if group.caption is not None:
                caption_owners.setdefault(group.caption.caption_id, []).append(
                    group.figure_id
                )
        competed = {
            caption_id
            for caption_id, owners in caption_owners.items()
            if len(owners) > 1
        }
        claimed = set(caption_owners)
        page_decisions: list[RegionRenderDecision] = []
        for group in page_groups:
            if group.caption is not None and group.caption.caption_id in competed:
                page_decisions.append(
                    RegionRenderDecision(
                        status="rejected",
                        page_index=page.page_index,
                        figure_id=group.figure_id,
                        reason="caption_competed_by_multiple_figures",
                        evidence_element_ids=group.caption.element_ids,
                    )
                )
                continue
            decision = _auto_decision_for_group(
                page, group, page_captions, claimed
            )
            if decision is not None:
                page_decisions.append(decision)

        requested_by_caption: dict[str, list[RegionRenderDecision]] = {}
        for decision in page_decisions:
            if decision.status == "requested" and decision.request is not None:
                requested_by_caption.setdefault(
                    decision.request.caption_id, []
                ).append(decision)
        conflicts = {
            item.figure_id
            for items in requested_by_caption.values()
            if len(items) > 1
            for item in items
        }
        for decision in page_decisions:
            if decision.figure_id in conflicts:
                decisions.append(
                    RegionRenderDecision(
                        status="rejected",
                        page_index=decision.page_index,
                        figure_id=decision.figure_id,
                        reason="caption_competed_by_multiple_figures",
                        evidence_element_ids=decision.evidence_element_ids,
                    )
                )
            else:
                decisions.append(decision)

        # Pure-vector pages currently have no native Figure group/asset to
        # retain.  Record a refusal instead of silently promoting a page crop.
        if not page_groups and page_captions:
            vectors = sorted(
                (
                    item.element_id
                    for item in page.elements
                    if item.kind == "vector"
                )
            )
            if len(vectors) >= _MIN_VECTOR_EVIDENCE:
                decisions.append(
                    RegionRenderDecision(
                        status="rejected",
                        page_index=page.page_index,
                        figure_id=None,
                        reason=(
                            "pure_vector_without_native_figure_group_not_auto_rendered"
                        ),
                        evidence_element_ids=tuple(vectors[:128]),
                    )
                )

    requested = sorted(
        (item for item in decisions if item.status == "requested"),
        key=lambda item: (
            item.page_index,
            item.request.bbox.y if item.request else 0.0,
            item.request.bbox.x if item.request else 0.0,
            item.figure_id or "",
        ),
    )
    allowed = {id(item) for item in requested[:max_candidates]}
    limited: list[RegionRenderDecision] = []
    for decision in decisions:
        if decision.status == "requested" and id(decision) not in allowed:
            limited.append(
                RegionRenderDecision(
                    status="rejected",
                    page_index=decision.page_index,
                    figure_id=decision.figure_id,
                    reason="document_candidate_limit_exceeded",
                    evidence_element_ids=decision.evidence_element_ids,
                )
            )
        else:
            limited.append(decision)
    return tuple(limited)


def plan_region_renders(
    document: PhysicalDocument,
    analysis: FigureAnalysis,
    *,
    mode: str = "explicit",
    max_candidates: int = 12,
) -> tuple[RegionRenderDecision, ...]:
    """Plan conservative region renders without paper/page special cases."""

    if mode == "explicit":
        return _plan_explicit_renders(document, analysis)
    if mode == "auto":
        return _plan_auto_renders(
            document, analysis, max_candidates=max_candidates
        )
    if mode == "off":
        return ()
    raise ValueError(f"unsupported region render mode: {mode}")
