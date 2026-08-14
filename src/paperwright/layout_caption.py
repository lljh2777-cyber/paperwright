"""Deterministic Figure/Table caption binding for reviewed layouts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Sequence

from .layout_models import FinalLayout, LayoutRegion
from .models import Page, PhysicalDocument


CaptionTextResolver = Callable[[Page, LayoutRegion], str]


@dataclass(frozen=True)
class CaptionBinding:
    caption_page_index: int
    caption_region_id: str
    visual_page_index: int
    visual_region_id: str
    method: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_page": self.caption_page_index + 1,
            "caption_region_id": self.caption_region_id,
            "visual_page": self.visual_page_index + 1,
            "visual_region_id": self.visual_region_id,
            "method": self.method,
            "score": round(self.score, 6),
        }


def _horizontal_overlap_ratio(left: LayoutRegion, right: LayoutRegion) -> float:
    overlap = max(
        0.0,
        min(left.bbox.right, right.bbox.right)
        - max(left.bbox.x, right.bbox.x),
    )
    return overlap / min(left.bbox.width, right.bbox.width)


def _caption_prefix_kind(text: str) -> str | None:
    normalized = text.lstrip("*# ")
    if re.match(r"^(?:fig(?:ure)?\.?)(?:\s|\d)", normalized, re.IGNORECASE):
        return "figure"
    if re.match(r"^table(?:\s|\d)", normalized, re.IGNORECASE):
        return "table"
    return None


def bind_caption_regions(
    document: PhysicalDocument,
    layouts: Sequence[FinalLayout],
    *,
    caption_text: CaptionTextResolver,
) -> tuple[
    dict[tuple[int, str], CaptionBinding],
    dict[tuple[int, str], CaptionBinding],
    dict[str, Any],
]:
    """Bind caption regions to visual regions using page-local geometry."""

    visuals: list[tuple[int, LayoutRegion]] = []
    captions: list[tuple[int, LayoutRegion, str]] = []
    for page, layout in zip(document.pages, layouts, strict=True):
        for region in layout.regions:
            if region.content_class == "visual" and region.role in {
                "figure",
                "table",
            }:
                visuals.append((page.page_index, region))
            elif region.content_class == "text" and region.role == "caption":
                captions.append(
                    (page.page_index, region, caption_text(page, region))
                )

    by_caption: dict[tuple[int, str], CaptionBinding] = {}
    by_visual: dict[tuple[int, str], CaptionBinding] = {}
    for caption_page, caption, text in captions:
        prefix_kind = _caption_prefix_kind(text)
        candidates: list[tuple[float, int, LayoutRegion, str]] = []
        for visual_page, visual in visuals:
            visual_key = (visual_page, visual.region_id)
            if visual_key in by_visual:
                continue
            role_bonus = 8.0 if prefix_kind == visual.role else 0.0
            if visual_page == caption_page:
                overlap = _horizontal_overlap_ratio(visual, caption)
                if overlap < 0.45:
                    continue
                below_gap = caption.bbox.y - visual.bbox.bottom
                above_gap = visual.bbox.y - caption.bbox.bottom
                gap = (
                    min(
                        value
                        for value in (below_gap, above_gap)
                        if value >= -0.02
                    )
                    if below_gap >= -0.02 or above_gap >= -0.02
                    else 1.0
                )
                if gap > 0.12:
                    continue
                order_gap = abs((caption.order or 0) - (visual.order or 0))
                if order_gap > 3:
                    continue
                score = (
                    100.0
                    + overlap * 20.0
                    - max(gap, 0.0) * 120.0
                    - order_gap * 2.0
                    + role_bonus
                )
                candidates.append(
                    (score, visual_page, visual, "same_page_geometry")
                )
            elif visual_page + 1 == caption_page:
                if (caption.order or 999) > 2 or caption.bbox.y > 0.26:
                    continue
                if visual.bbox.bottom < 0.72 and (
                    visual.bbox.width * visual.bbox.height < 0.42
                ):
                    continue
                score = (
                    72.0
                    + min(visual.bbox.width * visual.bbox.height, 0.9) * 20.0
                    - caption.bbox.y * 20.0
                    + role_bonus
                )
                candidates.append(
                    (score, visual_page, visual, "next_page_top_caption")
                )
        if not candidates:
            continue
        score, visual_page, visual, method = max(
            candidates,
            key=lambda item: (
                item[0],
                -item[1],
                -(item[2].order or 0),
                item[2].region_id,
            ),
        )
        binding = CaptionBinding(
            caption_page,
            caption.region_id,
            visual_page,
            visual.region_id,
            method,
            score,
        )
        by_caption[(caption_page, caption.region_id)] = binding
        by_visual[(visual_page, visual.region_id)] = binding

    unbound_captions = [
        {"page": page + 1, "region_id": region.region_id}
        for page, region, _ in captions
        if (page, region.region_id) not in by_caption
    ]
    unbound_visuals = [
        {"page": page + 1, "region_id": region.region_id, "role": region.role}
        for page, region in visuals
        if (page, region.region_id) not in by_visual
    ]
    summary = {
        "status": "pass" if not unbound_captions else "warning",
        "binding_count": len(by_caption),
        "caption_region_count": len(captions),
        "visual_region_count": len(visuals),
        "unbound_caption_count": len(unbound_captions),
        "unbound_visual_count": len(unbound_visuals),
        "unbound_visuals_are_informational": True,
        "bindings": [
            item.to_dict()
            for item in sorted(
                by_caption.values(),
                key=lambda value: (
                    value.caption_page_index,
                    value.caption_region_id,
                ),
            )
        ],
        "findings": (unbound_captions + unbound_visuals)[:50],
    }
    return by_caption, by_visual, summary
