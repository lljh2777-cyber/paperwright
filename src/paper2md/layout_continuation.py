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


def _cross_page_pair_is_continuation(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
    page_markers: dict[int, int],
) -> bool:
    marker = page_markers.get(current.page_index)
    return (
        _body_pair_has_continuation_text(previous, current)
        and current.page_index == previous.page_index + 1
        and marker is not None
        and previous.text_index + 2 == marker
        and current.trace_index == marker + 2
        and current.first_line_indent_state != "indented"
    )


def _same_page_pair_is_continuation(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
) -> bool:
    return (
        _body_pair_has_continuation_text(previous, current)
        and current.page_index == previous.page_index
        and current.region_id != previous.region_id
        and current.trace_index == previous.text_index + 2
        and current.first_line_indent_state == "aligned"
    )


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


def _paragraph_pair_continuation_kind(
    previous: CrossPageParagraphBlock,
    current: CrossPageParagraphBlock,
    page_markers: dict[int, int],
) -> str | None:
    if _caption_pair_is_continuation(previous, current):
        return "caption_internal_boundary"
    if _same_page_pair_is_continuation(previous, current):
        return "same_page_region_boundary"
    if _cross_page_pair_is_continuation(previous, current, page_markers):
        return "cross_page_boundary"
    return None


def merge_paragraph_continuations(
    lines: list[str],
    blocks: Sequence[CrossPageParagraphBlock],
    page_markers: dict[int, int],
) -> list[dict[str, Any]]:
    """Merge isolated caption fragments and high-confidence body continuations."""

    ordered = sorted(blocks, key=lambda item: item.trace_index)
    chains: list[list[CrossPageParagraphBlock]] = []
    index = 0
    while index < len(ordered) - 1:
        if _paragraph_pair_continuation_kind(
            ordered[index], ordered[index + 1], page_markers
        ) is None:
            index += 1
            continue
        chain = [ordered[index], ordered[index + 1]]
        index += 1
        while (
            index < len(ordered) - 1
            and _paragraph_pair_continuation_kind(
                ordered[index], ordered[index + 1], page_markers
            )
            is not None
        ):
            chain.append(ordered[index + 1])
            index += 1
        chains.append(chain)
        index += 1

    events: list[dict[str, Any]] = []
    for chain in reversed(chains):
        merged = chain[0].text
        boundary_records: list[dict[str, Any]] = []
        for previous, current in zip(chain, chain[1:]):
            method = _paragraph_pair_continuation_kind(
                previous,
                current,
                page_markers,
            )
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
        traces = [lines[item.trace_index] for item in chain]
        regions = ",".join(item.region_id for item in chain)
        replacement = traces + [
            f"<!-- paragraph-continuation: regions: {regions}; "
            "method: native-geometry -->",
            merged,
            "",
        ]
        lines[chain[0].trace_index : chain[-1].text_index + 2] = replacement
        events.extend(boundary_records)
    return list(reversed(events))
