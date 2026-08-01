"""Deterministic reconstruction of native PDF text objects.

The routines in this module never infer missing semantic content.  They only
normalise Unicode controls, join fragments using native geometry, and repair
line boundaries when the PDF provides explicit geometric evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable

from .models import Element


TEXT_RECONSTRUCTION_VERSION = "paper2md-native-text-reconstruction-v4"

_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)
_CLOSING_PUNCTUATION = ",.;:!?)]}%\u00b2\u00b3\u2020*"
_VISIBLE_HYPHENS = ("-", "\u2010", "\u2011")
_NONCHARACTERS = {"\ufffe", "\uffff"}
_PROSE_BOUNDARY_WORDS = frozenset(
    {"and", "or", "with", "within", "without", "from", "to", "in", "on", "by", "of"}
)


@dataclass(frozen=True)
class ReconstructionEvent:
    code: str
    before: str
    after: str
    element_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "before": self.before,
            "after": self.after,
            "element_ids": list(self.element_ids),
        }


@dataclass(frozen=True)
class ReconstructionWarning:
    code: str
    snippet: str
    codepoints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "snippet": self.snippet,
            "codepoints": list(self.codepoints),
        }


@dataclass(frozen=True)
class ReconstructedText:
    text: str
    element_ids: tuple[str, ...]
    events: tuple[ReconstructionEvent, ...] = ()
    warnings: tuple[ReconstructionWarning, ...] = ()
    first_line_indented: bool = False
    first_line_indent_state: str = "unknown"
    first_line_indent_offset: float | None = None


def clean_text(text: str) -> tuple[str, int]:
    """Normalise native text without compatibility-folding scientific glyphs."""

    removed = 0
    output: list[str] = []
    for character in unicodedata.normalize("NFC", text):
        if character in {"\r", "\n", "\t"}:
            output.append(" ")
            continue
        if character in {"\u00ad", "\u200b", "\ufeff"}:
            removed += 1
            continue
        if unicodedata.category(character) == "Cc":
            removed += 1
            continue
        output.append(character)
    value = "".join(output).replace("\u00a0", " ").translate(_LIGATURES)
    return re.sub(r"[ \t]+", " ", value), removed


def _intersection_over_smaller(left: Element, right: Element) -> float:
    width = max(
        0.0,
        min(left.bbox.right, right.bbox.right)
        - max(left.bbox.x, right.bbox.x),
    )
    height = max(
        0.0,
        min(left.bbox.bottom, right.bbox.bottom)
        - max(left.bbox.y, right.bbox.y),
    )
    smaller = min(
        left.bbox.width * left.bbox.height,
        right.bbox.width * right.bbox.height,
    )
    return width * height / smaller if smaller > 0 else 0.0


def _deduplicate(elements: Iterable[Element]) -> list[Element]:
    kept: list[Element] = []
    for element in sorted(
        elements,
        key=lambda item: (
            item.bbox.x,
            item.bbox.y,
            item.metadata.get("native_order", 0),
            item.element_id,
        ),
    ):
        value, _ = clean_text((element.text or "").strip())
        normalised = re.sub(r"\s+", " ", value).casefold()
        if not normalised:
            kept.append(element)
            continue
        duplicate_index: int | None = None
        replacement = False
        for index, existing in enumerate(kept):
            if _intersection_over_smaller(existing, element) < 0.85:
                continue
            existing_value, _ = clean_text((existing.text or "").strip())
            existing_normalised = re.sub(
                r"\s+", " ", existing_value
            ).casefold()
            if normalised == existing_normalised or normalised in existing_normalised:
                duplicate_index = index
                break
            if existing_normalised in normalised:
                duplicate_index = index
                replacement = True
                break
        if duplicate_index is None:
            kept.append(element)
        elif replacement:
            kept[duplicate_index] = element
    return sorted(kept, key=lambda item: (item.bbox.x, item.bbox.y))


def _suffix_prefix_overlap(left: str, right: str) -> int:
    for size in range(min(len(left), len(right)), 1, -1):
        if left[-size:].casefold() == right[:size].casefold():
            return size
    return 0


def _font_key(element: Element) -> str | None:
    value = element.metadata.get("font_name")
    if not isinstance(value, str):
        return None
    normalized = value.casefold().replace(" ", "")
    return normalized or None


def _vertical_overlap_ratio(left: Element, right: Element) -> float:
    overlap = max(
        0.0,
        min(left.bbox.bottom, right.bbox.bottom)
        - max(left.bbox.y, right.bbox.y),
    )
    height = min(left.bbox.height, right.bbox.height)
    return overlap / height if height > 0 else 0.0


def _font_style(font: str | None) -> str | None:
    if font is None:
        return None
    if "italic" in font or "oblique" in font:
        return "italic"
    return "roman"


def _is_greek_letter(character: str) -> bool:
    return bool(character) and (
        "GREEK" in unicodedata.name(character, "")
        and unicodedata.category(character).startswith("L")
    )


def _needs_geometric_word_space(
    value: str,
    fragment: str,
    left: Element,
    right: Element,
) -> bool:
    """Detect an omitted visible word gap without consulting vocabulary."""

    if not value or not fragment or not fragment[0].isalnum():
        return False
    if value.endswith(("%", "+")) and fragment[0].isalpha():
        return True
    left_font = _font_key(left)
    right_font = _font_key(right)
    left_style = _font_style(left_font)
    right_style = _font_style(right_font)
    signed_gap = right.bbox.x - left.bbox.right
    smaller_height = min(left.bbox.height, right.bbox.height)
    compact_prefix = value.rstrip()[:-1].rstrip()
    if (
        value[-1] in {"μ", "µ"}
        and compact_prefix[-1:].isdigit()
        and fragment.casefold().startswith(("g", "l", "m", "s"))
    ):
        return False
    next_word = re.match(r"[A-Za-z]+", fragment)
    if (
        re.search(r"\b[A-Z]{3,}$", value)
        and next_word is not None
        and next_word.group(0).casefold() in _PROSE_BOUNDARY_WORDS
        and _vertical_overlap_ratio(left, right) >= 0.65
        and signed_gap >= -min(1.0, smaller_height * 0.15)
    ):
        return True
    if (
        _is_greek_letter(value[-1])
        and fragment[0].isascii()
        and fragment[0].isalpha()
        and left_font is not None
        and right_font is not None
        and left_font != right_font
        and _vertical_overlap_ratio(left, right) >= 0.65
        and signed_gap >= -0.5
    ):
        return True
    return (
        value[-1].isalnum()
        and left_style is not None
        and right_style is not None
        and left_style != right_style
        and _vertical_overlap_ratio(left, right) >= 0.65
        and signed_gap >= -min(1.0, smaller_height * 0.15)
    )


def _letter_spaced_boundaries(
    elements: list[Element], fragments: list[str]
) -> set[int]:
    """Return boundaries that are letter spacing rather than word spacing.

    A boundary is collapsed only inside a run of at least three aligned,
    same-font uppercase fragments, with at least one single-letter fragment.
    This is deliberately stricter than simply matching spaced capitals.
    """

    eligible: list[bool] = []
    for index in range(len(elements) - 1):
        left = elements[index]
        right = elements[index + 1]
        left_text = fragments[index]
        right_text = fragments[index + 1]
        height = min(left.bbox.height, right.bbox.height)
        gap = right.bbox.x - left.bbox.right
        eligible.append(
            bool(re.fullmatch(r"[A-Z]{1,12}", left_text))
            and bool(re.fullmatch(r"[A-Z]{1,12}", right_text))
            and _font_key(left) is not None
            and _font_key(left) == _font_key(right)
            and _vertical_overlap_ratio(left, right) >= 0.82
            and 0.0 <= gap <= max(0.9, height * 0.34)
        )

    collapsed: set[int] = set()
    start = 0
    while start < len(eligible):
        if not eligible[start]:
            start += 1
            continue
        end = start
        while end + 1 < len(eligible) and eligible[end + 1]:
            end += 1
        fragment_slice = fragments[start : end + 2]
        if (
            len(fragment_slice) >= 3
            and any(len(item) == 1 for item in fragment_slice)
            and sum(len(item) for item in fragment_slice) >= 5
        ):
            collapsed.update(range(start, end + 1))
        start = end + 1
    return collapsed


def _unicode_warnings(text: str) -> tuple[ReconstructionWarning, ...]:
    suspicious = sorted(
        {
            character
            for character in text
            if character in _NONCHARACTERS
            or character == "\ufffd"
            or unicodedata.category(character) in {"Cs", "Co", "Cn"}
        },
        key=ord,
    )
    if not suspicious:
        return ()
    return (
        ReconstructionWarning(
            code="suspicious_unicode_codepoint",
            snippet=text[:160],
            codepoints=tuple(f"U+{ord(item):04X}" for item in suspicious),
        ),
    )


def join_line_elements(elements: list[Element]) -> ReconstructedText:
    source_element_ids = tuple(item.element_id for item in elements)
    native_values = {
        item.metadata["native_line_text"]
        for item in elements
        if isinstance(item.metadata.get("native_line_text"), str)
        and item.metadata["native_line_text"].strip()
    }
    if len(native_values) == 1:
        native = native_values.pop()
        cleaned, removed = clean_text(native)
        value = re.sub(r"\s+", " ", cleaned).strip()
        events = (
            ReconstructionEvent(
                "unicode_controls_removed",
                native,
                value,
                source_element_ids,
            ),
        ) if removed else ()
        return ReconstructedText(
            value,
            source_element_ids,
            events,
            _unicode_warnings(value),
        )

    kept = _deduplicate(elements)
    fragments: list[str] = []
    removed_controls = 0
    for element in kept:
        fragment, removed = clean_text((element.text or "").strip())
        fragments.append(fragment)
        removed_controls += removed
    collapsed = _letter_spaced_boundaries(kept, fragments)
    events: list[ReconstructionEvent] = []
    if collapsed:
        pending = sorted(collapsed)
        run_start = pending[0]
        run_end = pending[0]
        for boundary in pending[1:] + [pending[-1] + 2]:
            if boundary == run_end + 1:
                run_end = boundary
                continue
            element_end = run_end + 1
            events.append(
                ReconstructionEvent(
                    "collapsed_geometric_letter_spacing",
                    " ".join(fragments[run_start : element_end + 1]),
                    "".join(fragments[run_start : element_end + 1]),
                    tuple(item.element_id for item in kept[run_start : element_end + 1]),
                )
            )
            run_start = boundary
            run_end = boundary

    value = ""
    previous: Element | None = None
    previous_fragment = ""
    for index, (element, fragment) in enumerate(zip(kept, fragments, strict=True)):
        if not fragment:
            continue
        if not value:
            value = fragment
        elif index - 1 in collapsed:
            value += fragment
        elif value.endswith((*_VISIBLE_HYPHENS, "/")):
            value += fragment
        elif fragment[0] in _CLOSING_PUNCTUATION:
            value += fragment
        else:
            assert previous is not None
            signed_gap = element.bbox.x - previous.bbox.right
            gap = max(0.0, signed_gap)
            overlap = (
                _suffix_prefix_overlap(value, fragment) if signed_gap < 0 else 0
            )
            if overlap:
                value += fragment[overlap:]
                previous = element
                previous_fragment = fragment
                continue
            smaller_height = min(previous.bbox.height, element.bbox.height)
            old_compact_limit = max(0.75, smaller_height * 0.12)
            same_font = (
                _font_key(previous) is not None
                and _font_key(previous) == _font_key(element)
            )
            compact_limit = (
                max(0.90, smaller_height * 0.16)
                if same_font
                else old_compact_limit
            )
            if (
                re.fullmatch(r"\d+(?:,\d+)*", previous_fragment)
                and previous.bbox.height < element.bbox.height * 0.75
                and fragment[0].isalpha()
            ):
                value += " " + fragment
            elif _needs_geometric_word_space(
                value,
                fragment,
                previous,
                element,
            ):
                before = value[-80:] + fragment[:80]
                value += " " + fragment
                events.append(
                    ReconstructionEvent(
                        "inserted_geometric_word_space",
                        before,
                        value[-(len(before) + 1) :],
                        (previous.element_id, element.element_id),
                    )
                )
            elif (
                gap <= compact_limit
                and _vertical_overlap_ratio(previous, element) >= 0.65
                and not value.endswith((",", ".", ";", ":", "!", "?"))
            ):
                value += fragment
                if (
                    same_font
                    and old_compact_limit < gap <= compact_limit
                    and previous_fragment[-1:].isalnum()
                    and fragment[0].isalnum()
                ):
                    events.append(
                        ReconstructionEvent(
                            "collapsed_tight_same_font_fragment_gap",
                            previous_fragment + " " + fragment,
                            previous_fragment + fragment,
                            (previous.element_id, element.element_id),
                        )
                    )
            else:
                value += " " + fragment
        previous = element
        previous_fragment = fragment

    value = re.sub(r"[ \t]+", " ", value).strip()
    if removed_controls:
        events.append(
            ReconstructionEvent(
                "unicode_controls_removed",
                " ".join((item.text or "") for item in kept),
                value,
                tuple(item.element_id for item in kept),
            )
        )
    return ReconstructedText(
        value,
        source_element_ids,
        tuple(events),
        _unicode_warnings(value),
    )


def _dominant_font(elements: list[Element]) -> str | None:
    widths: dict[str, float] = {}
    for element in elements:
        value = element.metadata.get("font_name")
        if isinstance(value, str) and value:
            widths[value] = widths.get(value, 0.0) + element.bbox.width
    return max(widths, key=widths.get) if widths else None


def _looks_like_heading(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return (
        len(text) <= 80
        and bool(letters)
        and sum(character.isupper() for character in letters) / len(letters) >= 0.85
    )


_PARAGRAPH_TERMINAL = re.compile(r"[.!?][\"'\u2019\u201d)]*(?:\d+)?$")


def _ends_paragraph_sentence(text: str) -> bool:
    return bool(_PARAGRAPH_TERMINAL.search(text.rstrip()))


def reconstruct_text_groups(
    elements: tuple[Element, ...],
) -> list[ReconstructedText]:
    line_groups: list[list[Element]] = []
    current_key: object = object()
    current: list[Element] = []
    for element in elements:
        if element.kind != "text" or not element.text:
            continue
        key = element.metadata.get("line_group", element.element_id)
        if current and key != current_key:
            line_groups.append(current)
            current = []
        current_key = key
        current.append(element)
    if current:
        line_groups.append(current)

    reconstructed_lines = [join_line_elements(line) for line in line_groups]
    paragraphs: list[list[int]] = []
    paragraph_indent_states: list[str] = []
    paragraph_indent_offsets: list[float | None] = []
    for line_index, line in enumerate(line_groups):
        if not paragraphs:
            paragraphs.append([line_index])
            paragraph_indent_states.append("unknown")
            paragraph_indent_offsets.append(None)
            continue
        previous_index = paragraphs[-1][-1]
        previous = line_groups[previous_index]
        previous_text = reconstructed_lines[previous_index].text
        current_text = reconstructed_lines[line_index].text
        previous_top = min(item.bbox.y for item in previous)
        previous_bottom = max(item.bbox.bottom for item in previous)
        current_top = min(item.bbox.y for item in line)
        previous_height = previous_bottom - previous_top
        current_height = max(item.bbox.bottom for item in line) - current_top
        vertical_gap = current_top - previous_bottom
        previous_start = min(item.bbox.x for item in previous)
        current_start = min(item.bbox.x for item in line)
        signed_indent = current_start - previous_start
        indent_delta = abs(signed_indent)
        previous_font = _dominant_font(previous)
        current_font = _dominant_font(line)
        same_font = (
            previous_font is None
            or current_font is None
            or previous_font == current_font
        )
        normal_line_gap = -1.0 <= vertical_gap <= max(
            5.0,
            min(previous_height, current_height) * 0.9,
        )
        indentation_threshold = max(
            4.0,
            min(previous_height, current_height) * 0.55,
        )
        indented_paragraph_start = (
            normal_line_gap
            and same_font
            and signed_indent >= indentation_threshold
            and _ends_paragraph_sentence(previous_text)
            and not _looks_like_heading(previous_text)
            and not _looks_like_heading(current_text)
        )
        if (
            len(paragraphs[-1]) == 1
            and normal_line_gap
            and same_font
            and not _looks_like_heading(previous_text)
            and not _looks_like_heading(current_text)
        ):
            first_line_offset = -signed_indent
            if first_line_offset >= indentation_threshold:
                paragraph_indent_states[-1] = "indented"
                paragraph_indent_offsets[-1] = first_line_offset
            elif abs(first_line_offset) < indentation_threshold:
                paragraph_indent_states[-1] = "aligned"
                paragraph_indent_offsets[-1] = first_line_offset
        continues = (
            normal_line_gap
            and indent_delta
            <= max(14.0, min(previous_height, current_height) * 2.0)
            and same_font
            and not indented_paragraph_start
            and not _looks_like_heading(previous_text)
            and not _looks_like_heading(current_text)
        )
        if continues:
            paragraphs[-1].append(line_index)
        else:
            paragraphs.append([line_index])
            paragraph_indent_states.append(
                "indented" if indented_paragraph_start else "unknown"
            )
            paragraph_indent_offsets.append(
                signed_indent if indented_paragraph_start else None
            )

    results: list[ReconstructedText] = []
    for paragraph, indent_state, indent_offset in zip(
        paragraphs,
        paragraph_indent_states,
        paragraph_indent_offsets,
        strict=True,
    ):
        text = ""
        element_ids: list[str] = []
        events: list[ReconstructionEvent] = []
        warnings: list[ReconstructionWarning] = []
        for position, line_index in enumerate(paragraph):
            line_result = reconstructed_lines[line_index]
            line_text = line_result.text
            element_ids.extend(line_result.element_ids)
            events.extend(line_result.events)
            warnings.extend(line_result.warnings)
            if not line_text:
                continue
            if not text:
                text = line_text
                continue
            previous_line = line_groups[paragraph[position - 1]]
            has_soft_break_marker = any(
                any(
                    unicodedata.category(character) == "Cc"
                    for character in (item.text or "")
                )
                for item in previous_line
            )
            if has_soft_break_marker:
                next_token = line_text.split(maxsplit=1)[0]
                joiner = (
                    "-"
                    if "-" in next_token
                    and text
                    and text[-1].isalnum()
                    and line_text[0].isalnum()
                    else ""
                )
                events.append(
                    ReconstructionEvent(
                        "joined_explicit_pdf_soft_break",
                        text[-80:] + " | " + line_text[:80],
                        text[-80:] + joiner + line_text[:80],
                        tuple(
                            item.element_id
                            for item in previous_line + line_groups[line_index]
                        ),
                    )
                )
            elif text.endswith(_VISIBLE_HYPHENS) and line_text[0].isalpha():
                joiner = ""
                events.append(
                    ReconstructionEvent(
                        "joined_line_after_visible_hyphen",
                        text[-80:] + " | " + line_text[:80],
                        text[-80:] + line_text[:80],
                        tuple(
                            item.element_id
                            for item in previous_line + line_groups[line_index]
                        ),
                    )
                )
            else:
                joiner = " "
            text += joiner + line_text
        value = re.sub(r"[ \t]+", " ", text).strip()
        warnings.extend(_unicode_warnings(value))
        unique_warnings = {
            (item.code, item.snippet, item.codepoints): item for item in warnings
        }
        results.append(
            ReconstructedText(
                value,
                tuple(element_ids),
                tuple(events),
                tuple(unique_warnings.values()),
                indent_state == "indented",
                indent_state,
                indent_offset,
            )
        )
    return results
