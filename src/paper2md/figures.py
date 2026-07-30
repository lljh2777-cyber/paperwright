"""Deterministic, page-local Figure and caption reconstruction.

The module deliberately performs no semantic image understanding.  It groups
native image placements using same-page geometry, pairs explicit ``Figure`` /
``Fig.`` text markers conservatively, and preserves every native element in the
PhysicalDocument and manifest provenance.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import dataclass
from statistics import median
from typing import Iterable

from PIL import Image

from .backends.base import ExtractedAsset
from .models import BBox, Element, Page, PhysicalDocument

_CAPTION_PREFIX = re.compile(
    r"^\s*(?:figure|fig\.?)\s*([0-9]+[a-z]?)\b[\s.:|—-]*",
    re.IGNORECASE,
)
_MAX_CAPTION_GAP = 110.0
_AMBIGUITY_DELTA = 0.08
_TINY_AREA_RATIO = 0.002
_NO_CAPTION_AREA_RATIO = 0.015
_STRONG_GROUP_GAP = 12.0
_MAX_COMPOSITE_PIXELS = 16_000_000


@dataclass(frozen=True)
class TextLine:
    line_id: str
    page_index: int
    element_ids: tuple[str, ...]
    text: str
    bbox: BBox


@dataclass(frozen=True)
class CaptionCandidate:
    caption_id: str
    page_index: int
    label: str
    element_ids: tuple[str, ...]
    text: str
    bbox: BBox


@dataclass(frozen=True)
class FigureGroup:
    figure_id: str
    page_index: int
    member_element_ids: tuple[str, ...]
    bbox: BBox
    caption_status: str
    caption: CaptionCandidate | None
    caption_confidence: float | None
    caption_reason: str
    extraction_mode: str
    evidence_status: str
    degraded_reasons: tuple[str, ...]
    vector_evidence_element_ids: tuple[str, ...]
    vector_evidence_count: int
    vector_evidence_sha256: str | None


@dataclass(frozen=True)
class FigureAnalysis:
    groups: tuple[FigureGroup, ...]
    rejections: tuple[dict[str, object], ...]
    caption_candidates: tuple[CaptionCandidate, ...]


def _union_bbox(items: Iterable[BBox]) -> BBox:
    boxes = tuple(items)
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return BBox(left, top, right - left, bottom - top)


def _horizontal_overlap(a: BBox, b: BBox) -> float:
    overlap = max(0.0, min(a.right, b.right) - max(a.x, b.x))
    return overlap / max(1e-9, min(a.width, b.width))


def _box_gap(a: BBox, b: BBox) -> tuple[float, float]:
    return (
        max(a.x - b.right, b.x - a.right, 0.0),
        max(a.y - b.bottom, b.y - a.bottom, 0.0),
    )


def _text_lines(page: Page) -> list[TextLine]:
    grouped: list[list[Element]] = []
    text = sorted(
        (
            element
            for element in page.elements
            if element.kind == "text" and element.text
        ),
        key=lambda item: (
            item.bbox.y + item.bbox.height / 2,
            item.bbox.x,
            item.element_id,
        ),
    )
    for element in text:
        selected: list[Element] | None = None
        center = element.bbox.y + element.bbox.height / 2
        for group in reversed(grouped[-20:]):
            if (
                _CAPTION_PREFIX.match(element.text or "")
                and any(_CAPTION_PREFIX.match(item.text or "") for item in group)
            ):
                continue
            group_box = _union_bbox(item.bbox for item in group)
            group_center = group_box.y + group_box.height / 2
            if abs(center - group_center) > max(
                2.5, min(element.bbox.height, group_box.height) * 0.5
            ):
                continue
            horizontal_gap = max(
                group_box.x - element.bbox.right,
                element.bbox.x - group_box.right,
                0.0,
            )
            if horizontal_gap <= 18.0:
                selected = group
                break
        if selected is None:
            grouped.append([element])
        else:
            selected.append(element)
    result: list[TextLine] = []
    for sequence, elements in enumerate(
        sorted(
            grouped,
            key=lambda group: (
                min(item.bbox.y for item in group),
                min(item.bbox.x for item in group),
            ),
        )
    ):
        ordered = sorted(elements, key=lambda item: (item.bbox.x, item.element_id))
        text = " ".join((item.text or "").strip() for item in ordered).strip()
        result.append(
            TextLine(
                line_id=f"p{page.page_index:04d}-line-{sequence:05d}",
                page_index=page.page_index,
                element_ids=tuple(item.element_id for item in ordered),
                text=re.sub(r"\s+", " ", text),
                bbox=_union_bbox(item.bbox for item in ordered),
            )
        )
    return result


def _caption_candidates(page: Page) -> list[CaptionCandidate]:
    lines = _text_lines(page)
    candidates: list[CaptionCandidate] = []
    for index, line in enumerate(lines):
        match = _CAPTION_PREFIX.match(line.text)
        if not match:
            continue
        # Parenthesized subfigure references such as "Fig 2c)" are body
        # references, not caption starts.
        if re.match(r"\s*\)", line.text[match.end() :]):
            continue
        selected = [line]
        previous = line
        for following in lines[index + 1 : index + 8]:
            if _CAPTION_PREFIX.match(following.text):
                break
            gap = following.bbox.y - previous.bbox.bottom
            if gap < -1.0 or gap > max(8.0, previous.bbox.height * 1.6):
                break
            if _horizontal_overlap(line.bbox, following.bbox) < 0.2:
                break
            selected.append(following)
            previous = following
        candidates.append(
            CaptionCandidate(
                caption_id=f"p{page.page_index:04d}-caption-{len(candidates) + 1:03d}",
                page_index=page.page_index,
                label=match.group(1).casefold(),
                element_ids=tuple(
                    element_id for item in selected for element_id in item.element_ids
                ),
                text=" ".join(item.text for item in selected),
                bbox=_union_bbox(item.bbox for item in selected),
            )
        )
    return candidates


def _immediate_score(image: Element, caption: CaptionCandidate, page: Page) -> float | None:
    overlap = _horizontal_overlap(image.bbox, caption.bbox)
    if overlap <= 0.05:
        return None
    gap = caption.bbox.y - image.bbox.bottom
    if -18.0 <= gap <= _MAX_CAPTION_GAP:
        return 0.65 + 0.25 * overlap + 0.10 * (1.0 - max(gap, 0.0) / _MAX_CAPTION_GAP)
    page_area = page.width * page.height
    inside = image.bbox.y <= caption.bbox.y <= image.bbox.bottom
    if inside and image.bbox.width * image.bbox.height / page_area >= 0.2:
        return 0.55 + 0.25 * overlap
    return None


def _strongly_connected(a: Element, b: Element) -> bool:
    x_gap, y_gap = _box_gap(a.bbox, b.bbox)
    if x_gap <= _STRONG_GROUP_GAP and y_gap <= _STRONG_GROUP_GAP:
        return True
    return (
        _horizontal_overlap(a.bbox, b.bbox) >= 0.65
        and y_gap <= _STRONG_GROUP_GAP * 2
    )


def _clusters(images: list[Element]) -> list[list[Element]]:
    remaining = set(item.element_id for item in images)
    by_id = {item.element_id: item for item in images}
    result: list[list[Element]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group = [by_id[seed]]
        changed = True
        while changed:
            changed = False
            for candidate_id in sorted(remaining):
                candidate = by_id[candidate_id]
                if any(_strongly_connected(candidate, member) for member in group):
                    remaining.remove(candidate_id)
                    group.append(candidate)
                    changed = True
                    break
        result.append(sorted(group, key=lambda item: (item.bbox.y, item.bbox.x, item.element_id)))
    return result


def _vector_evidence(page: Page, bbox: BBox) -> tuple[tuple[str, ...], int, str | None]:
    ids = sorted(
        element.element_id
        for element in page.elements
        if element.kind == "vector"
        and bbox.x <= element.bbox.x + element.bbox.width / 2 <= bbox.right
        and bbox.y <= element.bbox.y + element.bbox.height / 2 <= bbox.bottom
    )
    if not ids:
        return (), 0, None
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return tuple(ids[:128]), len(ids), digest


def analyze_figures(document: PhysicalDocument) -> FigureAnalysis:
    groups: list[FigureGroup] = []
    rejections: list[dict[str, object]] = []
    all_captions: list[CaptionCandidate] = []
    for page in document.pages:
        images = [item for item in page.elements if item.kind == "image"]
        captions = _caption_candidates(page)
        all_captions.extend(captions)
        if not images:
            continue
        page_area = page.width * page.height
        substantial = [
            item
            for item in images
            if item.bbox.width * item.bbox.height / page_area >= _TINY_AREA_RATIO
        ]
        tiny = [item for item in images if item not in substantial]

        # Only markers with immediate image evidence become caption anchors.
        anchors: list[CaptionCandidate] = []
        anchor_scores: dict[tuple[str, str], float] = {}
        for caption in captions:
            for image in substantial:
                score = _immediate_score(image, caption, page)
                if score is not None:
                    anchors.append(caption)
                    anchor_scores[(image.element_id, caption.caption_id)] = score
            if caption in anchors:
                continue
        anchors = list({item.caption_id: item for item in anchors}.values())

        assigned: dict[str, list[Element]] = {item.caption_id: [] for item in anchors}
        ambiguous: list[Element] = []
        unassigned: list[Element] = []
        for image in substantial:
            scored: list[tuple[float, CaptionCandidate]] = []
            for caption in anchors:
                score = anchor_scores.get((image.element_id, caption.caption_id))
                if score is None:
                    # Once a caption has immediate evidence, include other
                    # substantial fragments in its same-column vertical band.
                    if image.bbox.bottom > caption.bbox.y + 18.0:
                        continue
                    vertical_gap = caption.bbox.y - image.bbox.bottom
                    overlap = _horizontal_overlap(image.bbox, caption.bbox)
                    if vertical_gap > 450.0 or overlap <= 0.05:
                        continue
                    score = 0.35 + 0.35 * overlap + 0.30 * (
                        1.0 - max(vertical_gap, 0.0) / 450.0
                    )
                scored.append((score, caption))
            scored.sort(key=lambda item: (-item[0], item[1].caption_id))
            if not scored:
                unassigned.append(image)
            elif len(scored) > 1 and scored[0][0] - scored[1][0] <= _AMBIGUITY_DELTA:
                ambiguous.append(image)
            else:
                assigned[scored[0][1].caption_id].append(image)

        page_groups: list[tuple[list[Element], str, CaptionCandidate | None, float | None, str]] = []
        for caption in anchors:
            members = assigned[caption.caption_id]
            if not members:
                continue
            scores = [
                anchor_scores.get((member.element_id, caption.caption_id), 0.55)
                for member in members
            ]
            confidence = min(0.99, max(0.85, sum(scores) / len(scores)))
            page_groups.append(
                (
                    sorted(members, key=lambda item: (item.bbox.y, item.bbox.x, item.element_id)),
                    "matched",
                    caption,
                    confidence,
                    "same_page_explicit_marker_and_geometry",
                )
            )
        for cluster in _clusters(ambiguous):
            page_groups.append(
                (cluster, "ambiguous", None, None, "multiple_caption_candidates_within_score_delta")
            )
        for cluster in _clusters(unassigned):
            cluster_bbox = _union_bbox(item.bbox for item in cluster)
            area_ratio = cluster_bbox.width * cluster_bbox.height / page_area
            if area_ratio < _NO_CAPTION_AREA_RATIO:
                for item in cluster:
                    rejections.append(
                        {
                            "element_id": item.element_id,
                            "page": page.page_index + 1,
                            "bbox": item.bbox.to_dict(),
                            "reason": "small_unpaired_native_image",
                            "evidence_status": "degraded",
                        }
                    )
                continue
            page_groups.append(
                (cluster, "none", None, None, "no_same_page_caption_candidate")
            )
        for item in tiny:
            rejections.append(
                {
                    "element_id": item.element_id,
                    "page": page.page_index + 1,
                    "bbox": item.bbox.to_dict(),
                    "reason": "tiny_or_publisher_mark_without_caption",
                    "evidence_status": "filtered_from_figure_candidates",
                }
            )

        page_groups.sort(
            key=lambda item: (
                min(member.bbox.y for member in item[0]),
                min(member.bbox.x for member in item[0]),
                item[0][0].element_id,
            )
        )
        for sequence, (members, status, caption, confidence, reason) in enumerate(
            page_groups, start=1
        ):
            bbox = _union_bbox(item.bbox for item in members)
            vector_ids, vector_count, vector_hash = _vector_evidence(page, bbox)
            degraded_reasons: list[str] = []
            if status != "matched":
                degraded_reasons.append(f"caption_{status}")
            extraction_mode = "embedded" if len(members) == 1 else "grouped"
            if extraction_mode == "grouped" and vector_count:
                degraded_reasons.append("vector_evidence_not_rendered")
            groups.append(
                FigureGroup(
                    figure_id=f"fig-p{page.page_index + 1:03d}-{sequence:03d}",
                    page_index=page.page_index,
                    member_element_ids=tuple(item.element_id for item in members),
                    bbox=bbox,
                    caption_status=status,
                    caption=caption,
                    caption_confidence=confidence,
                    caption_reason=reason,
                    extraction_mode=extraction_mode,
                    evidence_status=(
                        "degraded_bitmap_group_with_unrendered_vector_evidence"
                        if extraction_mode == "grouped" and vector_count
                        else "complete_native_bitmap_group"
                        if status == "matched"
                        else "degraded"
                    ),
                    degraded_reasons=tuple(degraded_reasons),
                    vector_evidence_element_ids=vector_ids,
                    vector_evidence_count=vector_count,
                    vector_evidence_sha256=vector_hash,
                )
            )
    return FigureAnalysis(tuple(groups), tuple(rejections), tuple(all_captions))


def compose_group_png(
    group: FigureGroup,
    *,
    elements_by_id: dict[str, Element],
    assets_by_element: dict[str, ExtractedAsset],
) -> tuple[bytes, int, int]:
    """Compose native bitmaps in PDF geometry without rendering the page."""

    members = [elements_by_id[item] for item in group.member_element_ids]
    scales = []
    for member in members:
        asset = assets_by_element[member.element_id]
        scales.extend(
            [
                asset.width_px / member.bbox.width,
                asset.height_px / member.bbox.height,
            ]
        )
    scale = max(0.25, min(4.0, median(scales)))
    width = max(1, int(round(group.bbox.width * scale)))
    height = max(1, int(round(group.bbox.height * scale)))
    if width * height > _MAX_COMPOSITE_PIXELS:
        scale *= math.sqrt(_MAX_COMPOSITE_PIXELS / (width * height))
        width = max(1, int(round(group.bbox.width * scale)))
        height = max(1, int(round(group.bbox.height * scale)))
    canvas = Image.new("RGB", (width, height), "white")
    for member in sorted(
        members,
        key=lambda item: (
            int(item.metadata.get("raw_object_index", 0)),
            item.element_id,
        ),
    ):
        asset = assets_by_element[member.element_id]
        with Image.open(io.BytesIO(asset.data)) as image:
            tile = image.convert("RGB")
            target = (
                max(1, int(round(member.bbox.width * scale))),
                max(1, int(round(member.bbox.height * scale))),
            )
            if tile.size != target:
                tile = tile.resize(target, Image.Resampling.LANCZOS)
            offset = (
                int(round((member.bbox.x - group.bbox.x) * scale)),
                int(round((member.bbox.y - group.bbox.y) * scale)),
            )
            canvas.paste(tile, offset)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue(), width, height
