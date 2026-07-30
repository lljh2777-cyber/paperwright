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


def plan_region_renders(
    document: PhysicalDocument,
    analysis: FigureAnalysis,
) -> tuple[RegionRenderDecision, ...]:
    """Return conservative page-local render requests and explicit refusals."""

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
