"""Deterministic caption and body paragraph continuation rules."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any, Sequence

from .models import Element


@dataclass(frozen=True)
class CrossPageParagraphBlock:
    page_index: int
    region_id: str
    trace_index: int
    text_index: int
    text: str
    role: str
    is_bold: bool
    dominant_font: str | None
    ends_with_pdf_soft_break: bool
    element_ids: tuple[str, ...]
    first_line_indented: bool = False
    first_line_indent_state: str = "unknown"
    first_line_indent_offset: float | None = None
    caption_binding_key: tuple[int, str] | None = None
    # Union bbox of the paragraph elements, normalized to the page (0..1).
    bbox: tuple[float, float, float, float] | None = None


def dominant_font_name(elements: Sequence[Element]) -> str | None:
    widths: Counter[str] = Counter()
    for element in elements:
        value = element.metadata.get("font_name")
        if isinstance(value, str) and value:
            widths[value.casefold()] += max(element.bbox.width, 0.0)
    return max(widths, key=widths.get) if widths else None


def _body_pair_has_continuation_text(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> bool:
    first = current.text.lstrip().removeprefix("&emsp;")[:1]
    return (
        previous.role == "body"
        and current.role == "body"
        and not previous.is_bold
        and not current.is_bold
        and bool(first)
        and first.islower()
        and not previous.text.rstrip().endswith((".", "!", "?", ":", ";"))
        and previous.dominant_font is not None
        and previous.dominant_font == current.dominant_font
    )


def _column_crossing_geometry(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> bool:
    """True when `current` sits in a column to the right of `previous` and
    begins above where `previous` ends — the signature of text that flows
    from the bottom of one column into the top of the next.

    Blocks without geometry (legacy fixtures) never pass: same-page
    column-crossing joins require real coordinates.
    """
    previous_box = previous.bbox
    current_box = current.bbox
    if previous_box is None or current_box is None:
        return False
    previous_center = (previous_box[0] + previous_box[2]) / 2.0
    current_center = (current_box[0] + current_box[2]) / 2.0
    return (
        current_center - previous_center > 0.1
        and current_box[1] < previous_box[3] + 0.05
    )


def _body_pair_continuation_kind(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> str | None:
    """Classify a pair of body blocks that are adjacent in the body reading
    order. Page furniture (figures, captions, page markers) may sit between
    the two blocks in the raw trace lines without blocking the join.
    """
    if not _body_pair_has_continuation_text(previous, current):
        return None
    if current.page_index == previous.page_index:
        if current.region_id == previous.region_id:
            return None
        if current.first_line_indent_state == "indented":
            return None
        if current.trace_index == previous.text_index + 2:
            # Nothing between the fragments in the trace: a region-boundary
            # split within the same flow.
            return "same_page_region_boundary"
        if _column_crossing_geometry(previous, current):
            # Page furniture between them: only join when the flow crosses
            # into a column to the right (the figure is a full-width band).
            return "same_page_region_boundary"
        return None
    if current.page_index == previous.page_index + 1:
        if current.first_line_indent_state == "indented":
            return None
        return "cross_page_boundary"
    return None


_CAPTION_PANEL_START = re.compile(
    r"^(?:\(?[A-Za-z]\)?[.,:]|[a-z][\u2013-][a-z][.,:])(?:\s|$)"
)


def _caption_pair_is_continuation(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> bool:
    current_text = current.text.lstrip().removeprefix("&emsp;")
    previous_text = previous.text.rstrip().rstrip("*_`")
    starts_new_caption = bool(
        re.match(
            r"^(?:fig(?:ure)?\.?|table)\s+S?\d+",
            current_text,
            re.IGNORECASE,
        )
    )
    return (
        previous.role == "caption"
        and current.role == "caption"
        and previous.page_index == current.page_index
        and previous.region_id == current.region_id
        and previous.caption_binding_key is not None
        and previous.caption_binding_key == current.caption_binding_key
        and current.trace_index == previous.text_index + 2
        and bool(current_text)
        and not starts_new_caption
        and (
            not previous_text.endswith((".", "!", "?", ":", ";"))
            or bool(_CAPTION_PANEL_START.match(current_text))
        )
    )


def _pair_kind(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> str | None:
    if (
        previous.role == "caption"
        and current.role == "caption"
        and _caption_pair_is_continuation(previous, current)
    ):
        return "caption_internal_boundary"
    if previous.role == "body" and current.role == "body":
        return _body_pair_continuation_kind(previous, current)
    return None


_EDGE_STRIP_TOP_RATIO = 0.08
_EDGE_STRIP_BOTTOM_RATIO = 0.92
_EDGE_STRIP_MAX_WORDS = 12
_EDGE_STRIP_MAX_LENGTH = 80


def _looks_like_page_edge_strip(block: CrossPageParagraphBlock) -> bool:
    """Short text in the extreme page band (running headers/footers and page
    numbers that furniture marking conservatively keeps, e.g. one-page
    variants). Such blocks never join chains and never block adjacency."""
    box = block.bbox
    if box is None:
        return False
    text = block.text.strip()
    if len(text) > _EDGE_STRIP_MAX_LENGTH or len(text.split()) > _EDGE_STRIP_MAX_WORDS:
        return False
    return box[3] < _EDGE_STRIP_TOP_RATIO or box[1] > _EDGE_STRIP_BOTTOM_RATIO


def _looks_like_all_caps_heading(block: CrossPageParagraphBlock) -> bool:
    """ALL-CAPS short lines are headings (or heading fragments) in flows
    without explicit heading roles; they never join chains."""
    alpha = [c for c in block.text if c.isalpha()]
    if not alpha or len(block.text.split()) > 10:
        return False
    return sum(c.isupper() for c in alpha) / len(alpha) > 0.7


_CAPTION_START = re.compile(
    r"^(?:fig(?:ure)?\.?|table)\s+S?\d+", re.IGNORECASE
)
_CAPTION_PANEL_LEAD = re.compile(r"^\([A-Za-z0-9]\)[\s.,:]")


def _looks_like_caption_fragment(block: CrossPageParagraphBlock) -> bool:
    """Caption lines that figure binding did not attach (e.g. panel-led
    continuation fragments). They sit between body fragments like captions:
    transparent, never joining chains."""
    text = block.text.lstrip().removeprefix("&emsp;").lstrip()
    return bool(
        _CAPTION_PANEL_LEAD.match(text)
        or _CAPTION_PANEL_START.match(text)
        or _CAPTION_START.match(text)
    )


def merge_paragraph_continuations(
    lines: list[str],
    blocks: Sequence[CrossPageParagraphBlock],
    page_markers: dict[int, int],
) -> list[dict[str, Any]]:
    """Merge isolated caption fragments and high-confidence body continuations.

    Body pairs must be adjacent in the body reading order: captions, figure
    lines, page markers and short page-edge strips between them do not block
    the join, while headings and other roles are barriers. Same-page pairs
    join when the fragments are directly adjacent in the trace (a
    region-boundary split within the same flow) or when page furniture sits
    between them and the flow crosses into a column to the right; a figure
    embedded within a single column remains a hard barrier.

    `page_markers` is retained for interface compatibility; cross-page joins
    no longer depend on marker positions.
    """

    ordered = sorted(blocks, key=lambda item: item.trace_index)

    # Candidate pairs in trace order. Captions, caption fragments, and short
    # page-edge strips are transparent (they sit between fragments of one
    # paragraph); headings and other roles are barriers that break the flow.
    pending: CrossPageParagraphBlock | None = None
    blocked = False
    pairs: list[tuple[CrossPageParagraphBlock, CrossPageParagraphBlock]] = []
    for block in ordered:
        candidate = (
            block.role == "body"
            and not _looks_like_page_edge_strip(block)
            and not _looks_like_all_caps_heading(block)
            and not _looks_like_caption_fragment(block)
        )
        transparent = (
            block.role == "caption"
            or _looks_like_page_edge_strip(block)
            or _looks_like_caption_fragment(block)
        )
        if candidate:
            if pending is not None and not blocked:
                pairs.append((pending, block))
            pending = block
            blocked = False
        elif not transparent:
            blocked = True

    chains: list[list[CrossPageParagraphBlock]] = []
    # Body chains: maximal runs of adjacent classified pairs.
    index = 0
    while index < len(pairs):
        if _pair_kind(*pairs[index]) is None:
            index += 1
            continue
        chain = [pairs[index][0], pairs[index][1]]
        index += 1
        while (
            index < len(pairs)
            and pairs[index][0] is chain[-1]
            and _pair_kind(*pairs[index]) is not None
        ):
            chain.append(pairs[index][1])
            index += 1
        chains.append(chain)
    # Caption chains: adjacent caption fragments in the same bound region.
    index = 0
    while index < len(ordered) - 1:
        if not _caption_pair_is_continuation(
            ordered[index], ordered[index + 1]
        ):
            index += 1
            continue
        chain = [ordered[index], ordered[index + 1]]
        index += 1
        while (
            index < len(ordered) - 1
            and _caption_pair_is_continuation(
                ordered[index], ordered[index + 1]
            )
        ):
            chain.append(ordered[index + 1])
            index += 1
        chains.append(chain)
        index += 1

    # Rebuild `lines` in one pass. In-place index surgery is unsafe here:
    # a chain's head slot and its members' slots are spread across the array
    # (page markers, captions and figures sit between them), so one chain's
    # replacement shifts the indices that another chain's members still need.
    # Instead, compute the full edit plan and rebuild the list linearly.
    events: list[dict[str, Any]] = []
    edits: list[tuple[int, int, list[str] | None]] = []
    for chain in chains:
        merged = chain[0].text
        boundary_records: list[dict[str, Any]] = []
        for previous, current in zip(chain, chain[1:]):
            method = _pair_kind(previous, current)
            assert method is not None
            joiner = (
                ""
                if previous.ends_with_pdf_soft_break
                or merged.endswith(("-", "‐", "‑"))
                else " "
            )
            boundary_records.append(
                {
                    "code": (
                        "joined_caption_fragment"
                        if method == "caption_internal_boundary"
                        else (
                            "joined_same_page_body_continuation"
                            if method == "same_page_region_boundary"
                            else "joined_cross_page_paragraph"
                        )
                    ),
                    "method": method,
                    "from_page": previous.page_index + 1,
                    "to_page": current.page_index + 1,
                    "from_region_id": previous.region_id,
                    "to_region_id": current.region_id,
                    "joiner": "none" if not joiner else "space",
                    "source_element_ids": list(
                        previous.element_ids + current.element_ids
                    ),
                }
            )
            merged += joiner + current.text
        head = chain[0]
        regions = ",".join(item.region_id for item in chain)
        edits.append(
            (
                head.trace_index,
                head.text_index + 2,
                [
                    lines[head.trace_index],
                    f"<!-- paragraph-continuation: regions: {regions}; "
                    "method: native-geometry -->",
                    merged,
                    "",
                ],
            )
        )
        for member in chain[1:]:
            edits.append((member.trace_index, member.text_index + 2, None))
        events.extend(boundary_records)

    rebuilt: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        if start < cursor:
            continue
        rebuilt.extend(lines[cursor:start])
        if replacement is not None:
            rebuilt.extend(replacement)
        cursor = end
    rebuilt.extend(lines[cursor:])
    lines[:] = rebuilt
    return list(reversed(events))
