"""Feature extraction for deterministic page-layout candidates."""

from __future__ import annotations

from collections import defaultdict
import math
import re
from typing import Sequence

from .models import BBox, Element, Page


_FIGURE_PREFIX = re.compile(r"^\s*(?:fig(?:ure)?\.?)\s*\d", re.IGNORECASE)
_TABLE_PREFIX = re.compile(r"^\s*table\s*\d", re.IGNORECASE)
_PANEL_LABEL = re.compile(r"^\s*[A-J]\s*$")
_NUMBER_TOKEN = re.compile(r"^[+\-−]?(?:\d+(?:[.,]\d+)?|\.\d+)%?$")


def _text_for(element: Element) -> str:
    native_line = element.metadata.get("native_line_text")
    if isinstance(native_line, str) and native_line.strip():
        return native_line.strip()
    return (element.text or "").strip()


def _union_bbox(boxes: Sequence[BBox]) -> BBox:
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return BBox(left, top, right - left, bottom - top)


def _merged_intervals(
    intervals: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _rectangle_union_area(boxes: Sequence[BBox]) -> float:
    if not boxes:
        return 0.0
    xs = sorted({item.x for item in boxes} | {item.right for item in boxes})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        if right <= left:
            continue
        intervals = [
            (item.y, item.bottom)
            for item in boxes
            if item.x < right and item.right > left
        ]
        height = sum(
            end - start for start, end in _merged_intervals(intervals)
        )
        area += (right - left) * height
    return area


def _variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((item - mean) ** 2 for item in values) / len(values)


def _line_features(elements: Sequence[Element]) -> tuple[int, float]:
    text = [
        item for item in elements if item.kind == "text" and _text_for(item)
    ]
    if not text:
        return 0, 0.0
    grouped: dict[tuple[str, int | str], list[Element]] = defaultdict(list)
    for item in text:
        line_group = item.metadata.get("line_group")
        key: tuple[str, int | str]
        if isinstance(line_group, int):
            key = ("line_group", line_group)
        else:
            key = ("element", item.element_id)
        grouped[key].append(item)
    lines = sorted(
        (_union_bbox([item.bbox for item in group]) for group in grouped.values()),
        key=lambda item: (item.y, item.x),
    )
    heights = [item.height for item in lines]
    centers = [item.y + item.height / 2 for item in lines]
    gaps = [
        max(0.0, following - previous)
        for previous, following in zip(centers, centers[1:])
    ]
    height_mean = max(sum(heights) / len(heights), 1e-6)
    height_cv = math.sqrt(_variance(heights)) / height_mean
    if gaps:
        gap_mean = max(sum(gaps) / len(gaps), 1e-6)
        gap_cv = math.sqrt(_variance(gaps)) / gap_mean
    else:
        gap_cv = 1.0
    count_factor = min(1.0, len(lines) / 4)
    regularity = count_factor * max(
        0.0,
        1.0 - min(1.0, (height_cv + gap_cv) / 2),
    )
    return len(lines), regularity


def candidate_features(
    page: Page,
    bbox: BBox,
    element_ids: Sequence[str],
    *,
    page_has_native_text: bool,
    peripheral_hint: bool,
    furniture_reason: str | None,
    band_index: int,
    column_index: int,
) -> dict[str, object]:
    """Calculate stable numeric and pattern features for one candidate."""

    selected_ids = set(element_ids)
    elements = [item for item in page.elements if item.element_id in selected_ids]
    area = bbox.width * bbox.height
    page_area = page.width * page.height
    text = [item for item in elements if item.kind == "text"]
    images = [item for item in elements if item.kind == "image"]
    vectors = [item for item in elements if item.kind == "vector"]
    text_coverage = (
        _rectangle_union_area([item.bbox for item in text]) / area
        if page_has_native_text
        else None
    )
    line_count, line_regularity = _line_features(text)
    font_names = {
        value
        for item in text
        if isinstance((value := item.metadata.get("font_name")), str) and value
    }
    font_sizes = [
        float(value)
        for item in text
        if isinstance((value := item.metadata.get("font_size")), (int, float))
        and math.isfinite(float(value))
    ]
    text_value = " ".join(_text_for(item) for item in text).strip()
    tokens = text_value.split()
    numeric_tokens = sum(bool(_NUMBER_TOKEN.fullmatch(item)) for item in tokens)
    return {
        "width_ratio": bbox.width / page.width,
        "height_ratio": bbox.height / page.height,
        "area_ratio": area / page_area,
        "aspect_ratio": bbox.width / bbox.height,
        "distance_left": bbox.x / page.width,
        "distance_right": (page.width - bbox.right) / page.width,
        "distance_top": bbox.y / page.height,
        "distance_bottom": (page.height - bbox.bottom) / page.height,
        "generation_band": band_index,
        "generation_column": column_index,
        "page_native_text_available": page_has_native_text,
        "region_has_native_text": bool(text),
        "native_text_coverage": text_coverage,
        "regular_line_coverage": (
            text_coverage * line_regularity
            if text_coverage is not None
            else None
        ),
        "scattered_text_coverage": (
            text_coverage * (1.0 - line_regularity)
            if text_coverage is not None
            else None
        ),
        "text_line_count": line_count,
        "line_regularity": line_regularity,
        "font_count": len(font_names),
        "font_size_mean": (
            sum(font_sizes) / len(font_sizes) if font_sizes else None
        ),
        "font_size_variance": _variance(font_sizes) if font_sizes else None,
        "bold_ratio": (
            sum(
                "bold" in str(item.metadata.get("font_name", "")).casefold()
                for item in text
            )
            / len(text)
            if text
            else None
        ),
        "image_count": len(images),
        "image_coverage": _rectangle_union_area(
            [item.bbox for item in images]
        )
        / area,
        "drawing_count": len(vectors),
        "drawing_coverage": _rectangle_union_area(
            [item.bbox for item in vectors]
        )
        / area,
        "starts_with_figure": bool(_FIGURE_PREFIX.match(text_value)),
        "starts_with_table": bool(_TABLE_PREFIX.match(text_value)),
        "panel_label_count": sum(
            bool(_PANEL_LABEL.fullmatch(_text_for(item))) for item in text
        ),
        "numeric_token_ratio": numeric_tokens / len(tokens) if tokens else 0.0,
        "peripheral_hint": peripheral_hint,
        "furniture_reason": furniture_reason,
    }
