"""Deterministic page-layout candidate generation.

This stage proposes reviewable regions from native PDF geometry.  It performs
no OCR and makes no semantic claim that a candidate is body text, a figure, or
a table.  Those decisions belong to the review and validation stages.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Mapping, Sequence

from .layout_models import (
    LayoutCandidate,
    LayoutPage,
    LayoutSeparator,
    LayoutTask,
    LAYOUT_TASK_VERSION,
    NormalizedBBox,
    RASTER_LAYOUT_TASK_VERSION,
)
from .models import BBox, Element, Page, PhysicalDocument
from .raster_layout import RasterPageAnalysis
from .references import is_reference_heading

CANDIDATE_GENERATOR_VERSION = "paper2md-whitespace-candidates-v0.4"
FEATURE_SCHEMA_VERSION = "paper2md-layout-features-v0.1"
RASTER_CANDIDATE_GENERATOR_VERSION = (
    "paper2md-whitespace-raster-candidates-v0.1"
)
RASTER_FEATURE_SCHEMA_VERSION = "paper2md-layout-features-v0.2"

_PAGE_NUMBER = re.compile(
    r"^\s*(?:page\s+)?(?:\d+|[ivxlcdm]+)(?:\s+(?:of|/)\s+\d+)?\s*$",
    re.IGNORECASE,
)
_FIGURE_PREFIX = re.compile(r"^\s*(?:fig(?:ure)?\.?)\s*\d", re.IGNORECASE)
_TABLE_PREFIX = re.compile(r"^\s*table\s*\d", re.IGNORECASE)
_PANEL_LABEL = re.compile(r"^\s*[A-J]\s*$")
_NUMBER_TOKEN = re.compile(r"^[+\-−]?(?:\d+(?:[.,]\d+)?|\.\d+)%?$")


@dataclass(frozen=True)
class CandidateGenerationConfig:
    header_footer_ratio: float = 0.07
    page_number_ratio: float = 0.04
    repeated_page_fraction: float = 0.5
    horizontal_gap_line_ratio: float = 0.9
    horizontal_gap_page_ratio: float = 0.006
    vertical_gap_line_ratio: float = 0.8
    vertical_gap_page_ratio: float = 0.012
    graphics_cluster_line_ratio: float = 0.6
    content_roi_padding_ratio: float = 0.005
    edge_band_limit_ratio: float = 0.12
    edge_band_max_height_ratio: float = 0.06
    edge_band_max_width_ratio: float = 0.65
    edge_band_gap_ratio: float = 0.006
    max_split_depth: int = 8

    def __post_init__(self) -> None:
        ratios = (
            self.header_footer_ratio,
            self.page_number_ratio,
            self.repeated_page_fraction,
            self.horizontal_gap_line_ratio,
            self.horizontal_gap_page_ratio,
            self.vertical_gap_line_ratio,
            self.vertical_gap_page_ratio,
            self.graphics_cluster_line_ratio,
            self.content_roi_padding_ratio,
            self.edge_band_limit_ratio,
            self.edge_band_max_height_ratio,
            self.edge_band_max_width_ratio,
            self.edge_band_gap_ratio,
        )
        if any(not math.isfinite(item) or item <= 0 for item in ratios):
            raise ValueError("候选生成阈值必须是正有限数")
        if self.header_footer_ratio >= 0.25:
            raise ValueError("header_footer_ratio 过大")
        if not 0 < self.repeated_page_fraction <= 1:
            raise ValueError("repeated_page_fraction 必须位于 (0,1]")
        if self.max_split_depth < 1:
            raise ValueError("max_split_depth 必须为正")


@dataclass(frozen=True)
class _Atom:
    atom_id: str
    bbox: BBox
    element_ids: tuple[str, ...]
    kinds: tuple[str, ...]
    text: str = ""
    features: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _Leaf:
    atoms: tuple[_Atom, ...]
    band_index: int
    column_index: int


def _intersect_bbox(left: BBox, right: BBox) -> BBox | None:
    x0 = max(left.x, right.x)
    y0 = max(left.y, right.y)
    x1 = min(left.right, right.right)
    y1 = min(left.bottom, right.bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return BBox(x0, y0, x1 - x0, y1 - y0)


def _padded_content_roi(
    page: Page,
    atoms: Sequence[_Atom],
    peripheral_ids: set[str],
    *,
    padding_ratio: float,
    outer_ratio: float,
    line_height: float,
    edge_band_limit_ratio: float,
    edge_band_max_height_ratio: float,
    edge_band_max_width_ratio: float,
    edge_band_gap_ratio: float,
) -> NormalizedBBox:
    peripheral_raster_boxes = [
        atom.bbox
        for atom in atoms
        if bool(atom.features.get("raster_peripheral_hint"))
    ]

    def covered_by_peripheral_raster(atom: _Atom) -> bool:
        if "raster" in atom.kinds:
            return bool(atom.features.get("raster_peripheral_hint"))
        area = atom.bbox.width * atom.bbox.height
        if area <= 0:
            return False
        return any(
            (
                max(
                    0.0,
                    min(atom.bbox.right, box.right)
                    - max(atom.bbox.x, box.x),
                )
                * max(
                    0.0,
                    min(atom.bbox.bottom, box.bottom)
                    - max(atom.bbox.y, box.y),
                )
                / area
                >= 0.50
            )
            for box in peripheral_raster_boxes
        )

    retained = [
        atom
        for atom in atoms
        if not covered_by_peripheral_raster(atom)
        and not (
            atom.element_ids
            and set(atom.element_ids).issubset(peripheral_ids)
            and (
                atom.bbox.y + atom.bbox.height / 2
                <= page.height * outer_ratio
                or atom.bbox.y + atom.bbox.height / 2
                >= page.height * (1 - outer_ratio)
            )
        )
    ]
    minimum_gap = max(
        line_height * 0.8,
        page.height * edge_band_gap_ratio,
    )

    # Remove isolated, shallow edge bands such as journal badges, running
    # labels, dates and folios.  This deliberately does not use pixel/OCR
    # content and preserves wider or multi-line footnotes.
    while len(retained) > 1:
        bands: list[list[_Atom]] = []
        band_bottoms: list[float] = []
        for atom in sorted(
            retained,
            key=lambda item: (item.bbox.y, item.bbox.x, item.atom_id),
        ):
            if not bands or atom.bbox.y - band_bottoms[-1] >= minimum_gap:
                bands.append([atom])
                band_bottoms.append(atom.bbox.bottom)
            else:
                bands[-1].append(atom)
                band_bottoms[-1] = max(band_bottoms[-1], atom.bbox.bottom)
        if len(bands) < 2:
            break
        ordered = sorted(
            bands,
            key=lambda band: (
                min(atom.bbox.y for atom in band),
                min(atom.bbox.x for atom in band),
            ),
        )
        first_box = _union_bbox(atom.bbox for atom in ordered[0])
        second_box = _union_bbox(atom.bbox for atom in ordered[1])
        last_box = _union_bbox(atom.bbox for atom in ordered[-1])
        previous_box = _union_bbox(atom.bbox for atom in ordered[-2])

        def removable(box: BBox, band: Sequence[_Atom]) -> bool:
            if any(
                atom.text and is_reference_heading(atom.text)
                for atom in band
            ):
                return False
            occupied_area = sum(
                atom.bbox.width * atom.bbox.height for atom in band
            )
            band_area = box.width * box.height
            occupancy = min(1.0, occupied_area / band_area)
            return (
                box.height
                <= max(
                    line_height * 2.0,
                    page.height * edge_band_max_height_ratio,
                )
                and (
                    box.width <= page.width * edge_band_max_width_ratio
                    or occupancy <= 0.45
                )
            )

        remove_band: Sequence[_Atom] | None = None
        if (
            first_box.bottom <= page.height * edge_band_limit_ratio
            and second_box.y - first_box.bottom >= minimum_gap
            and removable(first_box, ordered[0])
        ):
            remove_band = ordered[0]
        elif (
            last_box.y >= page.height * (1 - edge_band_limit_ratio)
            and last_box.y - previous_box.bottom >= minimum_gap
            and removable(last_box, ordered[-1])
        ):
            remove_band = ordered[-1]
        if remove_band is None:
            break
        removed_ids = {id(atom) for atom in remove_band}
        retained = [atom for atom in retained if id(atom) not in removed_ids]

    boxes = [atom.bbox for atom in retained]
    if not boxes:
        return NormalizedBBox(0.0, 0.0, 1.0, 1.0)
    content = _union_bbox(boxes)
    x_pad = page.width * padding_ratio
    y_pad = page.height * padding_ratio
    left = max(0.0, content.x - x_pad)
    top = max(0.0, content.y - y_pad)
    right = min(page.width, content.right + x_pad)
    bottom = min(page.height, content.bottom + y_pad)
    return NormalizedBBox.from_pdf_bbox(
        BBox(left, top, right - left, bottom - top),
        page_width=page.width,
        page_height=page.height,
    )


def _union_bbox(boxes: Iterable[BBox]) -> BBox:
    values = tuple(boxes)
    left = min(item.x for item in values)
    top = min(item.y for item in values)
    right = max(item.right for item in values)
    bottom = max(item.bottom for item in values)
    return BBox(left, top, right - left, bottom - top)


def _gap(a: BBox, b: BBox) -> tuple[float, float]:
    return (
        max(a.x - b.right, b.x - a.right, 0.0),
        max(a.y - b.bottom, b.y - a.bottom, 0.0),
    )


def _overlap_length(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _text_for(element: Element) -> str:
    native_line = element.metadata.get("native_line_text")
    if isinstance(native_line, str) and native_line.strip():
        return native_line.strip()
    return (element.text or "").strip()


def _text_atoms(page: Page) -> list[_Atom]:
    text = [
        item for item in page.elements if item.kind == "text" and _text_for(item)
    ]
    by_group: dict[int, list[Element]] = defaultdict(list)
    ungrouped: list[Element] = []
    for item in text:
        group = item.metadata.get("line_group")
        if isinstance(group, int):
            by_group[group].append(item)
        else:
            ungrouped.append(item)

    grouped: list[list[Element]] = list(by_group.values())
    for element in sorted(
        ungrouped,
        key=lambda item: (
            item.bbox.y + item.bbox.height / 2,
            item.bbox.x,
            item.element_id,
        ),
    ):
        selected: list[Element] | None = None
        center = element.bbox.y + element.bbox.height / 2
        for group in reversed(grouped[-24:]):
            group_box = _union_bbox(item.bbox for item in group)
            group_center = group_box.y + group_box.height / 2
            if abs(center - group_center) > max(
                2.5,
                min(element.bbox.height, group_box.height) * 0.45,
            ):
                continue
            horizontal_gap = max(
                group_box.x - element.bbox.right,
                element.bbox.x - group_box.right,
                0.0,
            )
            if horizontal_gap <= max(
                10.0,
                min(element.bbox.height, group_box.height) * 1.5,
            ):
                selected = group
                break
        if selected is None:
            grouped.append([element])
        else:
            selected.append(element)

    atoms: list[_Atom] = []
    ordered_groups = sorted(
        grouped,
        key=lambda group: (
            min(item.bbox.y for item in group),
            min(item.bbox.x for item in group),
            min(item.element_id for item in group),
        ),
    )
    for index, group in enumerate(ordered_groups):
        ordered = sorted(group, key=lambda item: (item.bbox.x, item.element_id))
        atoms.append(
            _Atom(
                atom_id=f"line-{index:05d}",
                bbox=_union_bbox(item.bbox for item in ordered),
                element_ids=tuple(item.element_id for item in ordered),
                kinds=("text",),
                text=" ".join(_text_for(item) for item in ordered).strip(),
            )
        )
    return atoms


class _DisjointSet:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _graphics_atoms(page: Page, proximity: float) -> list[_Atom]:
    graphics = [
        item for item in page.elements if item.kind in {"image", "vector"}
    ]
    if not graphics:
        return []

    cell_size = max(12.0, proximity * 3)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    disjoint = _DisjointSet(len(graphics))
    for index, item in enumerate(graphics):
        left = max(0.0, item.bbox.x - proximity)
        top = max(0.0, item.bbox.y - proximity)
        right = min(page.width, item.bbox.right + proximity)
        bottom = min(page.height, item.bbox.bottom + proximity)
        cells = {
            (x, y)
            for x in range(
                int(left // cell_size),
                int(right // cell_size) + 1,
            )
            for y in range(
                int(top // cell_size),
                int(bottom // cell_size) + 1,
            )
        }
        compared: set[int] = set()
        for cell in cells:
            for other_index in grid[cell]:
                if other_index in compared:
                    continue
                compared.add(other_index)
                other = graphics[other_index]
                x_gap, y_gap = _gap(item.bbox, other.bbox)
                if x_gap <= proximity and y_gap <= proximity:
                    disjoint.union(index, other_index)
        for cell in cells:
            grid[cell].append(index)

    groups: dict[int, list[Element]] = defaultdict(list)
    for index, item in enumerate(graphics):
        groups[disjoint.find(index)].append(item)
    result: list[_Atom] = []
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            min(item.bbox.y for item in group),
            min(item.bbox.x for item in group),
            min(item.element_id for item in group),
        ),
    )
    for index, group in enumerate(ordered_groups):
        ordered = sorted(group, key=lambda item: item.element_id)
        result.append(
            _Atom(
                atom_id=f"graphic-{index:05d}",
                bbox=_union_bbox(item.bbox for item in ordered),
                element_ids=tuple(item.element_id for item in ordered),
                kinds=tuple(sorted({item.kind for item in ordered})),
            )
        )
    return result


def _raster_atoms(
    page: Page,
    analysis: RasterPageAnalysis | None,
) -> list[_Atom]:
    if analysis is None:
        return []
    if analysis.page_index != page.page_index:
        raise ValueError("raster analysis page index does not match page")
    result: list[_Atom] = []
    for item in analysis.regions:
        bbox = item.bbox.to_pdf_bbox(
            page_width=page.width,
            page_height=page.height,
        )
        peripheral_hint = (
            (
                item.bbox.y <= 0.025
                or item.bbox.bottom >= 0.975
            )
            and item.bbox.height <= 0.06
            and item.bbox.width <= 0.40
            and item.page_area_ratio <= 0.02
        )
        result.append(
            _Atom(
                atom_id=f"raster-{item.region_id}",
                bbox=bbox,
                element_ids=(),
                kinds=("raster",),
                features={
                    "raster_ink_coverage": item.ink_coverage,
                    "raster_residual_coverage": item.residual_coverage,
                    "raster_text_mask_coverage": item.text_mask_coverage,
                    "raster_page_area_ratio": item.page_area_ratio,
                    "raster_peripheral_hint": peripheral_hint,
                },
            )
        )
    return result


def _other_atoms(page: Page) -> list[_Atom]:
    return [
        _Atom(
            atom_id=f"other-{index:05d}",
            bbox=item.bbox,
            element_ids=(item.element_id,),
            kinds=(item.kind,),
        )
        for index, item in enumerate(
            sorted(
                (
                    item
                    for item in page.elements
                    if item.kind not in {"text", "image", "vector"}
                ),
                key=lambda item: (item.bbox.y, item.bbox.x, item.element_id),
            )
        )
    ]


def _line_height(page: Page, text_atoms: Sequence[_Atom]) -> float:
    heights = [
        item.bbox.height
        for item in text_atoms
        if 0 < item.bbox.height < page.height * 0.15
    ]
    return median(heights) if heights else max(6.0, page.height * 0.012)


def _furniture_signature(value: str) -> str:
    compact = " ".join(value.casefold().split())
    return re.sub(r"\d+", "#", compact)


def _furniture_element_ids(
    document: PhysicalDocument,
    config: CandidateGenerationConfig,
) -> tuple[dict[int, set[str]], dict[int, dict[str, str]]]:
    lines_by_page = {page.page_index: _text_atoms(page) for page in document.pages}
    signatures: dict[str, set[int]] = defaultdict(set)
    eligible: dict[int, list[_Atom]] = defaultdict(list)
    for page in document.pages:
        for line in lines_by_page[page.page_index]:
            center = line.bbox.y + line.bbox.height / 2
            if (
                center <= page.height * config.header_footer_ratio
                or center >= page.height * (1 - config.header_footer_ratio)
            ):
                signature = _furniture_signature(line.text)
                if signature:
                    signatures[signature].add(page.page_index)
                    eligible[page.page_index].append(line)

    required = max(
        2,
        math.ceil(len(document.pages) * config.repeated_page_fraction),
    )
    furniture: dict[int, set[str]] = defaultdict(set)
    reasons: dict[int, dict[str, str]] = defaultdict(dict)
    for page in document.pages:
        for line in eligible[page.page_index]:
            center = line.bbox.y + line.bbox.height / 2
            signature = _furniture_signature(line.text)
            repeated = len(signatures.get(signature, ())) >= required
            outer_page_number = (
                _PAGE_NUMBER.fullmatch(line.text) is not None
                and (
                    center <= page.height * config.page_number_ratio
                    or center >= page.height * (1 - config.page_number_ratio)
                )
            )
            if not repeated and not outer_page_number:
                continue
            reason = (
                "repeated_header_footer"
                if repeated
                else "outer_page_number"
            )
            for element_id in line.element_ids:
                furniture[page.page_index].add(element_id)
                reasons[page.page_index][element_id] = reason
    return furniture, reasons


def _merged_intervals(
    intervals: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    ordered = sorted(intervals)
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(item[0], item[1]) for item in merged]


def _best_gap(
    atoms: Sequence[_Atom],
    *,
    axis: str,
    minimum: float,
    maximum_occupancy: float = 0.12,
) -> tuple[float, float] | None:
    """Find a low-occupancy corridor, tolerating sparse crossing elements."""

    if len(atoms) < 2:
        return None
    if axis == "y":
        start = min(item.bbox.y for item in atoms)
        end = max(item.bbox.bottom for item in atoms)
        orthogonal_start = min(item.bbox.x for item in atoms)
        orthogonal_end = max(item.bbox.right for item in atoms)
    else:
        start = min(item.bbox.x for item in atoms)
        end = max(item.bbox.right for item in atoms)
        orthogonal_start = min(item.bbox.y for item in atoms)
        orthogonal_end = max(item.bbox.bottom for item in atoms)
    extent = end - start
    orthogonal_extent = orthogonal_end - orthogonal_start
    if extent <= 0 or orthogonal_extent <= 0:
        return None
    step = max(0.5, extent / 1024)
    samples: list[tuple[float, float]] = []
    position = start + step / 2
    while position < end:
        if axis == "y":
            intervals = [
                (item.bbox.x, item.bbox.right)
                for item in atoms
                if item.bbox.y <= position <= item.bbox.bottom
            ]
        else:
            intervals = [
                (item.bbox.y, item.bbox.bottom)
                for item in atoms
                if item.bbox.x <= position <= item.bbox.right
            ]
        occupied = sum(
            interval_end - interval_start
            for interval_start, interval_end in _merged_intervals(intervals)
        )
        samples.append((position, occupied / orthogonal_extent))
        position += step

    corridors: list[tuple[float, float, float]] = []
    run: list[tuple[float, float]] = []
    for sample in samples:
        if sample[1] <= maximum_occupancy:
            run.append(sample)
            continue
        if run:
            corridor_start = max(start, run[0][0] - step / 2)
            corridor_end = min(end, run[-1][0] + step / 2)
            if corridor_end - corridor_start >= minimum:
                corridors.append(
                    (
                        corridor_start,
                        corridor_end,
                        sum(item[1] for item in run) / len(run),
                    )
                )
            run = []
    if run:
        corridor_start = max(start, run[0][0] - step / 2)
        corridor_end = min(end, run[-1][0] + step / 2)
        if corridor_end - corridor_start >= minimum:
            corridors.append(
                (
                    corridor_start,
                    corridor_end,
                    sum(item[1] for item in run) / len(run),
                )
            )
    internal = [
        item
        for item in corridors
        if item[0] > start + step and item[1] < end - step
    ]
    if not internal:
        return None
    selected = max(
        internal,
        key=lambda item: (
            (item[1] - item[0]) * (1.0 - item[2]),
            -item[2],
            -item[0],
        ),
    )
    return selected[0], selected[1]


def _split_at_gap(
    atoms: Sequence[_Atom],
    *,
    axis: str,
    gap: tuple[float, float],
) -> tuple[tuple[_Atom, ...], tuple[_Atom, ...]] | None:
    midpoint = (gap[0] + gap[1]) / 2
    if axis == "y":
        before = tuple(
            item
            for item in atoms
            if item.bbox.y + item.bbox.height / 2 < midpoint
        )
        after = tuple(item for item in atoms if item not in before)
    else:
        before = tuple(
            item
            for item in atoms
            if item.bbox.x + item.bbox.width / 2 < midpoint
        )
        after = tuple(item for item in atoms if item not in before)
    if not before or not after:
        return None
    return before, after


def _horizontal_bands(
    atoms: Sequence[_Atom],
    *,
    minimum_gap: float,
    max_depth: int,
) -> list[tuple[_Atom, ...]]:
    def visit(items: tuple[_Atom, ...], depth: int) -> list[tuple[_Atom, ...]]:
        if depth >= max_depth:
            return [items]
        gap = _best_gap(items, axis="y", minimum=minimum_gap)
        if gap is None:
            return [items]
        split = _split_at_gap(items, axis="y", gap=gap)
        if split is None:
            return [items]
        return visit(split[0], depth + 1) + visit(split[1], depth + 1)

    return sorted(
        visit(tuple(atoms), 0),
        key=lambda items: (
            min(item.bbox.y for item in items),
            min(item.bbox.x for item in items),
        ),
    )


def _vertical_columns(
    atoms: Sequence[_Atom],
    *,
    minimum_gap: float,
    max_depth: int,
) -> list[tuple[_Atom, ...]]:
    def visit(items: tuple[_Atom, ...], depth: int) -> list[tuple[_Atom, ...]]:
        if depth >= max_depth:
            return [items]
        gap = _best_gap(items, axis="x", minimum=minimum_gap)
        if gap is None:
            return [items]
        # Sparse full-width rules, affiliations, and other spanning objects may
        # cross an otherwise valid column gutter.  Assigning such an atom to a
        # side by its centre makes that side's candidate bbox cover both
        # columns, even though the text atoms were separated correctly.  Keep
        # true gutter-crossing atoms as their own horizontally grouped
        # candidates so review sees honest geometry.
        crossing = tuple(
            item
            for item in items
            if item.bbox.x < gap[0] and item.bbox.right > gap[1]
        )
        remaining = tuple(item for item in items if item not in crossing)
        split = _split_at_gap(remaining, axis="x", gap=gap)
        if split is None:
            return [items]
        spanning_groups = (
            _horizontal_bands(
                crossing,
                minimum_gap=minimum_gap,
                max_depth=max(1, max_depth - depth),
            )
            if crossing
            else []
        )
        return (
            visit(split[0], depth + 1)
            + visit(split[1], depth + 1)
            + spanning_groups
        )

    return sorted(
        visit(tuple(atoms), 0),
        key=lambda items: (
            min(item.bbox.x for item in items),
            min(item.bbox.y for item in items),
        ),
    )


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
        height = sum(end - start for start, end in _merged_intervals(intervals))
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
        (
            _Atom(
                atom_id=str(key),
                bbox=_union_bbox(item.bbox for item in group),
                element_ids=tuple(item.element_id for item in group),
                kinds=("text",),
            )
            for key, group in grouped.items()
        ),
        key=lambda item: (item.bbox.y, item.bbox.x),
    )
    heights = [item.bbox.height for item in lines]
    centers = [item.bbox.y + item.bbox.height / 2 for item in lines]
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
    regularity = count_factor * max(0.0, 1.0 - min(1.0, (height_cv + gap_cv) / 2))
    return len(lines), regularity


def _candidate_features(
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
    features: dict[str, object] = {
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
    return features


def _candidate_from_leaf(
    page: Page,
    leaf: _Leaf,
    *,
    candidate_id: str,
    page_has_native_text: bool,
    peripheral_hint: bool = False,
    furniture_reason: str | None = None,
) -> LayoutCandidate:
    bbox = _union_bbox(item.bbox for item in leaf.atoms)
    element_ids = tuple(
        sorted(
            {
                element_id
                for atom in leaf.atoms
                for element_id in atom.element_ids
            }
        )
    )
    kinds = tuple(
        sorted({kind for atom in leaf.atoms for kind in atom.kinds})
    )
    features = _candidate_features(
        page,
        bbox,
        element_ids,
        page_has_native_text=page_has_native_text,
        peripheral_hint=peripheral_hint,
        furniture_reason=furniture_reason,
        band_index=leaf.band_index,
        column_index=leaf.column_index,
    )
    return LayoutCandidate(
        candidate_id=candidate_id,
        bbox=NormalizedBBox.from_pdf_bbox(
            bbox,
            page_width=page.width,
            page_height=page.height,
        ),
        source_element_ids=element_ids,
        element_kinds=kinds,
        features=features,
    )


def _candidate_from_raster_atom(
    page: Page,
    atom: _Atom,
    *,
    candidate_id: str,
    page_has_native_text: bool,
) -> LayoutCandidate:
    features = _candidate_features(
        page,
        atom.bbox,
        (),
        page_has_native_text=page_has_native_text,
        peripheral_hint=bool(atom.features.get("raster_peripheral_hint")),
        furniture_reason=(
            "raster_edge_badge"
            if bool(atom.features.get("raster_peripheral_hint"))
            else None
        ),
        band_index=-1,
        column_index=-1,
    )
    features.update(
        {
            "raster_evidence": True,
            "raster_region_count": 1,
            "raster_ink_coverage_max": atom.features[
                "raster_ink_coverage"
            ],
            "raster_residual_coverage_max": atom.features[
                "raster_residual_coverage"
            ],
            "raster_text_mask_coverage_max": atom.features[
                "raster_text_mask_coverage"
            ],
        }
    )
    return LayoutCandidate(
        candidate_id=candidate_id,
        bbox=NormalizedBBox.from_pdf_bbox(
            atom.bbox,
            page_width=page.width,
            page_height=page.height,
        ),
        source_element_ids=(),
        element_kinds=("raster",),
        features=features,
    )


def _candidate_separators(
    candidates: Sequence[LayoutCandidate],
) -> tuple[LayoutSeparator, ...]:
    content = [
        item
        for item in candidates
        if not bool(item.features.get("peripheral_hint"))
    ]
    separators: list[tuple[str, str, str, NormalizedBBox, dict[str, object]]] = []
    for index, left in enumerate(content):
        for right in content[index + 1 :]:
            a = left.bbox
            b = right.bbox
            x_overlap = _overlap_length(a.x, a.right, b.x, b.right)
            y_overlap = _overlap_length(a.y, a.bottom, b.y, b.bottom)
            x_gap = max(a.x - b.right, b.x - a.right, 0.0)
            y_gap = max(a.y - b.bottom, b.y - a.bottom, 0.0)
            if y_gap > 0 and x_overlap / min(a.width, b.width) >= 0.25:
                top = min(a.bottom, b.bottom)
                bottom = max(a.y, b.y)
                bbox = NormalizedBBox(
                    max(a.x, b.x),
                    top,
                    x_overlap,
                    bottom - top,
                )
                separators.append(
                    (
                        "horizontal",
                        left.candidate_id,
                        right.candidate_id,
                        bbox,
                        {
                            "gap_ratio": y_gap,
                            "orthogonal_overlap_ratio": x_overlap
                            / min(a.width, b.width),
                        },
                    )
                )
            elif x_gap > 0 and y_overlap / min(a.height, b.height) >= 0.25:
                left_edge = min(a.right, b.right)
                right_edge = max(a.x, b.x)
                bbox = NormalizedBBox(
                    left_edge,
                    max(a.y, b.y),
                    right_edge - left_edge,
                    y_overlap,
                )
                separators.append(
                    (
                        "vertical",
                        left.candidate_id,
                        right.candidate_id,
                        bbox,
                        {
                            "gap_ratio": x_gap,
                            "orthogonal_overlap_ratio": y_overlap
                            / min(a.height, b.height),
                        },
                    )
                )
    ordered = sorted(
        separators,
        key=lambda item: (
            item[3].y,
            item[3].x,
            item[0],
            item[1],
            item[2],
        ),
    )
    return tuple(
        LayoutSeparator(
            separator_id=f"S{index:03d}",
            orientation=orientation,
            bbox=bbox,
            adjacent_candidate_ids=(left, right),
            features=features,
        )
        for index, (orientation, left, right, bbox, features) in enumerate(
            ordered,
            start=1,
        )
    )


def propose_content_rois(
    document: PhysicalDocument,
    *,
    config: CandidateGenerationConfig | None = None,
    raster_analyses: Mapping[int, RasterPageAnalysis] | None = None,
) -> dict[int, NormalizedBBox]:
    """Propose a conservative analysis ROI for every page.

    Repeated furniture and page numbers are excluded before the union is
    calculated.  The returned boxes remain in the original page coordinate
    system and are intended for AI/human confirmation, not destructive crop.
    """

    settings = config or CandidateGenerationConfig()
    raster_by_page = dict(raster_analyses or {})
    if raster_analyses is not None:
        expected = {page.page_index for page in document.pages}
        if set(raster_by_page) != expected:
            raise ValueError("raster analysis pages do not match document")
    furniture, _ = _furniture_element_ids(document, settings)
    proposals: dict[int, NormalizedBBox] = {}
    for page in document.pages:
        text_atoms = _text_atoms(page)
        line_height = _line_height(page, text_atoms)
        atoms = (
            text_atoms
            + _graphics_atoms(
                page,
                proximity=line_height * settings.graphics_cluster_line_ratio,
            )
            + _raster_atoms(page, raster_by_page.get(page.page_index))
            + _other_atoms(page)
        )
        proposals[page.page_index] = _padded_content_roi(
            page,
            atoms,
            furniture.get(page.page_index, set()),
            padding_ratio=settings.content_roi_padding_ratio,
            outer_ratio=settings.page_number_ratio,
            line_height=line_height,
            edge_band_limit_ratio=settings.edge_band_limit_ratio,
            edge_band_max_height_ratio=settings.edge_band_max_height_ratio,
            edge_band_max_width_ratio=settings.edge_band_max_width_ratio,
            edge_band_gap_ratio=settings.edge_band_gap_ratio,
        )
    return proposals


def generate_layout_tasks(
    document: PhysicalDocument,
    *,
    config: CandidateGenerationConfig | None = None,
    content_rois: Mapping[int, NormalizedBBox] | None = None,
    content_roi_source: str = "rule_proposed",
    raster_analyses: Mapping[int, RasterPageAnalysis] | None = None,
) -> tuple[LayoutTask, ...]:
    """Generate one deterministic, AI-reviewable layout task per page.

    Candidate splitting and separator detection operate only inside the page's
    content ROI.  Element and candidate coordinates remain normalized against
    the original, uncropped PDF page.
    """

    settings = config or CandidateGenerationConfig()
    raster_by_page = dict(raster_analyses or {})
    if raster_analyses is not None:
        expected = {page.page_index for page in document.pages}
        if set(raster_by_page) != expected:
            raise ValueError("raster analysis pages do not match document")
        for page_index, analysis in raster_by_page.items():
            if analysis.page_index != page_index:
                raise ValueError("raster analysis mapping key mismatch")
    furniture, furniture_reasons = _furniture_element_ids(document, settings)
    rois = (
        dict(content_rois)
        if content_rois is not None
        else propose_content_rois(
            document,
            config=settings,
            raster_analyses=raster_analyses,
        )
    )
    expected_pages = {page.page_index for page in document.pages}
    if set(rois) != expected_pages:
        missing = sorted(expected_pages - set(rois))
        extra = sorted(set(rois) - expected_pages)
        raise ValueError(
            f"content ROI pages do not match document; missing={missing}, extra={extra}"
        )
    if not content_roi_source.strip():
        raise ValueError("content_roi_source must not be empty")
    tasks: list[LayoutTask] = []
    for page in document.pages:
        text_atoms = _text_atoms(page)
        line_height = _line_height(page, text_atoms)
        atoms = (
            text_atoms
            + _graphics_atoms(
                page,
                proximity=line_height * settings.graphics_cluster_line_ratio,
            )
            + _raster_atoms(page, raster_by_page.get(page.page_index))
            + _other_atoms(page)
        )
        peripheral_ids = furniture.get(page.page_index, set())
        analysis_roi = rois[page.page_index]
        analysis_pdf_bbox = analysis_roi.to_pdf_bbox(
            page_width=page.width,
            page_height=page.height,
        )
        content_atoms: list[_Atom] = []
        excluded_element_ids: set[str] = set()
        boundary_crossing_element_ids: set[str] = set()
        for atom in atoms:
            atom_center = atom.bbox.y + atom.bbox.height / 2
            outer_furniture = (
                atom.element_ids
                and set(atom.element_ids).issubset(peripheral_ids)
                and (
                    atom_center
                    <= page.height * settings.page_number_ratio
                    or atom_center
                    >= page.height * (1 - settings.page_number_ratio)
                )
            )
            if outer_furniture:
                excluded_element_ids.update(atom.element_ids)
                continue
            intersection = _intersect_bbox(atom.bbox, analysis_pdf_bbox)
            if intersection is None:
                excluded_element_ids.update(atom.element_ids)
                continue
            if intersection != atom.bbox:
                boundary_crossing_element_ids.update(atom.element_ids)
            content_atoms.append(
                _Atom(
                    atom_id=atom.atom_id,
                    bbox=intersection,
                    element_ids=atom.element_ids,
                    kinds=atom.kinds,
                    text=atom.text,
                    features=dict(atom.features),
                )
            )

        structural_content_atoms = [
            atom for atom in content_atoms if "raster" not in atom.kinds
        ]
        raster_content_atoms = [
            atom
            for atom in content_atoms
            if "raster" in atom.kinds
            and not bool(atom.features.get("raster_peripheral_hint"))
        ]
        raster_suppressed_element_ids: set[str] = set()
        if raster_content_atoms:
            retained_structural_atoms: list[_Atom] = []
            for atom in structural_content_atoms:
                atom_area = atom.bbox.width * atom.bbox.height
                covered_ratio = (
                    max(
                        (
                            _overlap_length(
                                atom.bbox.x,
                                atom.bbox.right,
                                raster_atom.bbox.x,
                                raster_atom.bbox.right,
                            )
                            * _overlap_length(
                                atom.bbox.y,
                                atom.bbox.bottom,
                                raster_atom.bbox.y,
                                raster_atom.bbox.bottom,
                            )
                            / atom_area
                        )
                        for raster_atom in raster_content_atoms
                    )
                    if atom_area > 0
                    else 0.0
                )
                if covered_ratio >= 0.50:
                    raster_suppressed_element_ids.update(atom.element_ids)
                    continue
                retained_structural_atoms.append(atom)
            structural_content_atoms = retained_structural_atoms

        horizontal_gap = max(
            line_height * settings.horizontal_gap_line_ratio,
            page.height * settings.horizontal_gap_page_ratio,
        )
        vertical_gap = max(
            line_height * settings.vertical_gap_line_ratio,
            page.width * settings.vertical_gap_page_ratio,
        )
        leaves: list[_Leaf] = []
        for band_index, band in enumerate(
            _horizontal_bands(
                structural_content_atoms,
                minimum_gap=horizontal_gap,
                max_depth=settings.max_split_depth,
            )
            if structural_content_atoms
            else []
        ):
            columns = _vertical_columns(
                band,
                minimum_gap=vertical_gap,
                max_depth=settings.max_split_depth,
            )
            leaves.extend(
                _Leaf(tuple(column), band_index, column_index)
                for column_index, column in enumerate(columns)
            )

        page_has_native_text = bool(text_atoms)
        ordered_leaves = sorted(
            leaves,
            key=lambda leaf: (
                leaf.band_index,
                leaf.column_index,
                min(item.bbox.y for item in leaf.atoms),
                min(item.bbox.x for item in leaf.atoms),
            ),
        )
        candidates: list[LayoutCandidate] = []
        for index, leaf in enumerate(ordered_leaves, start=1):
            candidates.append(
                _candidate_from_leaf(
                    page,
                    leaf,
                    candidate_id=f"C{index:03d}",
                    page_has_native_text=page_has_native_text,
                )
            )
        next_candidate_index = len(candidates) + 1
        for raster_atom in sorted(
            raster_content_atoms,
            key=lambda item: (
                item.bbox.y,
                item.bbox.x,
                item.bbox.height,
                item.bbox.width,
                item.atom_id,
            ),
        ):
            candidates.append(
                _candidate_from_raster_atom(
                    page,
                    raster_atom,
                    candidate_id=f"C{next_candidate_index:03d}",
                    page_has_native_text=page_has_native_text,
                )
            )
            next_candidate_index += 1
        content_boxes = [
            item.bbox
            for item in candidates
        ]
        content_bbox = (
            _union_bbox(
                item.to_pdf_bbox(
                    page_width=page.width,
                    page_height=page.height,
                )
                for item in content_boxes
            )
            if content_boxes
            else None
        )
        task = LayoutTask(
            contract_version=(
                RASTER_LAYOUT_TASK_VERSION
                if raster_analyses is not None
                else LAYOUT_TASK_VERSION
            ),
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(page),
            candidate_generator_version=(
                RASTER_CANDIDATE_GENERATOR_VERSION
                if raster_analyses is not None
                else CANDIDATE_GENERATOR_VERSION
            ),
            feature_schema_version=(
                RASTER_FEATURE_SCHEMA_VERSION
                if raster_analyses is not None
                else FEATURE_SCHEMA_VERSION
            ),
            candidates=tuple(candidates),
            separators=_candidate_separators(candidates),
            metadata={
                "analysis_roi": {
                    "bbox": analysis_roi.to_dict(),
                    "source": content_roi_source,
                    "coordinate_system": "top-left/original-page-normalized/y-down",
                    "destructive_crop": False,
                },
                "content_bbox": (
                    NormalizedBBox.from_pdf_bbox(
                        content_bbox,
                        page_width=page.width,
                        page_height=page.height,
                    ).to_dict()
                    if content_bbox is not None
                    else None
                ),
                "horizontal_gap_threshold_pdf_points": horizontal_gap,
                "vertical_gap_threshold_pdf_points": vertical_gap,
                "line_height_median_pdf_points": line_height,
                "excluded_element_ids": sorted(excluded_element_ids),
                "boundary_crossing_element_ids": sorted(
                    boundary_crossing_element_ids
                ),
                "raster_suppressed_element_ids": sorted(
                    raster_suppressed_element_ids
                ),
                "furniture_reasons": {
                    element_id: reason
                    for element_id, reason in sorted(
                        furniture_reasons.get(page.page_index, {}).items()
                    )
                    if element_id in excluded_element_ids
                },
                "ocr_used": False,
                "raster_evidence": (
                    {
                        "contract_version": raster_by_page[
                            page.page_index
                        ].contract_version,
                        "preview_width": raster_by_page[
                            page.page_index
                        ].preview_width,
                        "preview_height": raster_by_page[
                            page.page_index
                        ].preview_height,
                        "ink_mask_sha256": raster_by_page[
                            page.page_index
                        ].ink_mask_sha256,
                        "text_mask_sha256": raster_by_page[
                            page.page_index
                        ].text_mask_sha256,
                        "residual_mask_sha256": raster_by_page[
                            page.page_index
                        ].residual_mask_sha256,
                        "region_count": len(
                            raster_by_page[page.page_index].regions
                        ),
                    }
                    if raster_analyses is not None
                    else None
                ),
            },
        )
        tasks.append(task)
    return tuple(tasks)
