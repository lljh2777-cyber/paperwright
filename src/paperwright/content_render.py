"""Conservative table and display-equation region-render planning.

This module intentionally mirrors ``region_render.py`` for Figure content: it
never renders a PDF, it only derives page-local clip requests from native text,
vector and caption evidence.  Tables and display equations are then embedded as
images while their native text remains available as degraded/searchable text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .figures import CaptionCandidate, _text_lines
from .models import BBox, Element, Page, PhysicalDocument
from .region_render import (
    REGION_RENDER_CAPTION_GUARD,
    REGION_RENDER_SCALE,
    RegionRenderRequest,
)

TABLE_CAPTION_PREFIX = re.compile(
    r"^\s*(?:table)\s*([0-9]+[a-z]?)\b[\s.:|—-]*",
    re.IGNORECASE,
)
_MATH_LINE = re.compile(r"[=+\-/×÷±()\[\]{}\d]", re.UNICODE)
_MIN_TABLE_LINES = 3
_MIN_TABLE_COLUMNS = 2
_TABLE_LOOKBACK_POINTS = 260.0
_TABLE_LOOKAHEAD_POINTS = 320.0
_EQUATION_MAX_HEIGHT_RATIO = 0.06
_EQUATION_MAX_WIDTH_RATIO = 0.72
_EQUATION_GAP_POINTS = 8.0


@dataclass(frozen=True)
class ContentRegionCandidate:
    content_id: str
    kind: str  # table | equation
    page_index: int
    bbox: BBox
    member_element_ids: tuple[str, ...]
    caption: CaptionCandidate | None
    reason: str


@dataclass(frozen=True)
class ContentRegionAnalysis:
    tables: tuple[ContentRegionCandidate, ...]
    equations: tuple[ContentRegionCandidate, ...]


def _caption_for(
    page: Page,
    *,
    prefix: re.Pattern[str],
    kind: str,
) -> tuple[CaptionCandidate, ...]:
    candidates: list[CaptionCandidate] = []
    for index, line in enumerate(_text_lines(page)):
        match = prefix.match(line.text)
        if not match:
            continue
        selected = [line]
        previous = line
        for following in _text_lines(page)[index + 1 : index + 8]:
            gap = following.bbox.y - previous.bbox.bottom
            if gap < -1.0 or gap > max(8.0, previous.bbox.height * 1.6):
                break
            selected.append(following)
            previous = following
        bboxes = [item.bbox for item in selected]
        candidates.append(
            CaptionCandidate(
                caption_id=(
                    f"p{page.page_index:04d}-{kind}-{len(candidates) + 1:03d}"
                ),
                page_index=page.page_index,
                label=match.group(1).casefold(),
                element_ids=tuple(
                    element_id
                    for item in selected
                    for element_id in item.element_ids
                ),
                text=" ".join(item.text for item in selected),
                bbox=BBox(
                    min(item.x for item in bboxes),
                    min(item.y for item in bboxes),
                    max(item.right for item in bboxes) - min(item.x for item in bboxes),
                    max(item.bottom for item in bboxes) - min(item.y for item in bboxes),
                ),
            )
        )
    return tuple(candidates)


def _union(elements: list[Element]) -> BBox:
    return BBox(
        min(item.bbox.x for item in elements),
        min(item.bbox.y for item in elements),
        max(item.bbox.right for item in elements) - min(item.bbox.x for item in elements),
        max(item.bbox.bottom for item in elements) - min(item.bbox.y for item in elements),
    )


def _table_candidates(page: Page) -> tuple[ContentRegionCandidate, ...]:
    captions = _caption_for(page, prefix=TABLE_CAPTION_PREFIX, kind="table")
    if not captions:
        return ()
    candidates: list[ContentRegionCandidate] = []
    claimed: set[str] = set()
    for caption in captions:
        claimed.update(caption.element_ids)
        rows = [
            item
            for item in _text_lines(page)
            if not set(item.element_ids) & claimed
            and item.bbox.bottom <= caption.bbox.y
            and caption.bbox.y - item.bbox.bottom <= _TABLE_LOOKBACK_POINTS
        ]
        if len(rows) < _MIN_TABLE_LINES:
            continue
        row_elements = [
            element
            for row in rows
            for element in page.elements
            if element.element_id in row.element_ids
        ]
        x_starts = sorted(
            {
                round(item.bbox.x / max(page.width, 1.0), 2)
                for item in row_elements
            }
        )
        if len(x_starts) < _MIN_TABLE_COLUMNS:
            continue
        row_union = _union(row_elements)
        vectors = [
            item
            for item in page.elements
            if item.kind == "vector"
            and item.bbox.bottom <= caption.bbox.y
            and item.bbox.bottom >= row_union.y
        ]
        union = _union(row_elements + vectors)
        if union.height <= 0 or union.width <= 0:
            continue
        candidates.append(
            ContentRegionCandidate(
                content_id=f"table-p{page.page_index:04d}-{len(candidates) + 1:03d}",
                kind="table",
                page_index=page.page_index,
                bbox=union,
                member_element_ids=tuple(
                    sorted({item.element_id for item in row_elements + vectors})
                ),
                caption=caption,
                reason="same_page_table_caption_and_columnar_native_lines",
            )
        )
    return tuple(candidates)


def _equation_candidates(page: Page) -> tuple[ContentRegionCandidate, ...]:
    lines = _text_lines(page)
    candidates: list[ContentRegionCandidate] = []
    for index, line in enumerate(lines):
        text = line.text.strip()
        if not text or len(text) > 80 or len(text.split()) > 20:
            continue
        if not _MATH_LINE.search(text):
            continue
        if line.bbox.height > page.height * _EQUATION_MAX_HEIGHT_RATIO:
            continue
        if line.bbox.width > page.width * _EQUATION_MAX_WIDTH_RATIO:
            continue
        center = line.bbox.x + line.bbox.width / 2
        if abs(center - page.width / 2) > page.width * 0.15:
            continue
        gaps: list[float] = []
        if index > 0:
            gaps.append(line.bbox.y - lines[index - 1].bbox.bottom)
        if index + 1 < len(lines):
            gaps.append(lines[index + 1].bbox.y - line.bbox.bottom)
        if not gaps or any(gap < _EQUATION_GAP_POINTS for gap in gaps):
            continue
        candidates.append(
            ContentRegionCandidate(
                content_id=f"equation-p{page.page_index:04d}-{len(candidates) + 1:03d}",
                kind="equation",
                page_index=page.page_index,
                bbox=line.bbox,
                member_element_ids=line.element_ids,
                caption=None,
                reason="centered_short_math_like_line_with_vertical_isolation",
            )
        )
    return tuple(candidates)


def analyze_content_regions(
    document: PhysicalDocument,
    *,
    max_candidates: int = 12,
) -> ContentRegionAnalysis:
    tables: list[ContentRegionCandidate] = []
    equations: list[ContentRegionCandidate] = []
    for page in document.pages:
        tables.extend(_table_candidates(page))
        equations.extend(_equation_candidates(page))
        if len(tables) + len(equations) >= max_candidates:
            break
    return ContentRegionAnalysis(
        tables=tuple(tables[:max_candidates]),
        equations=tuple(equations[:max_candidates]),
    )


def to_region_render_request(
    candidate: ContentRegionCandidate,
    page: Page,
) -> RegionRenderRequest:
    caption = candidate.caption
    return RegionRenderRequest(
        figure_id=candidate.content_id,
        page_index=candidate.page_index,
        bbox=candidate.bbox,
        caption_top=(
            caption.bbox.y if caption is not None else page.height
        ),
        caption_id=caption.caption_id if caption else "no-caption",
        caption_element_ids=caption.element_ids if caption else (),
        caption_text=caption.text if caption else "",
        caption_bbox=caption.bbox if caption else candidate.bbox,
        caption_reason="same_page_table_caption" if caption else "display_equation",
        caption_confidence=0.9 if caption else 0.8,
        member_element_ids=candidate.member_element_ids,
        vector_evidence_element_ids=(),
        vector_evidence_count=0,
        vector_evidence_sha256=hashlib.sha256(b"").hexdigest(),
        fallback_reason=f"native_{candidate.kind}_rendered_as_image",
        bbox_rule=candidate.reason,
        scale=REGION_RENDER_SCALE,
        caption_guard=(
            REGION_RENDER_CAPTION_GUARD if caption is not None else 0.0
        ),
    )


__all__ = [
    "ContentRegionAnalysis",
    "ContentRegionCandidate",
    "analyze_content_regions",
    "to_region_render_request",
]
