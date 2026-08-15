"""Deterministic fallback final-layout builder for L0-routed pages.

When routing decides a page does not need a visual model, the orchestrator can
still produce a valid visual-direct final-layout from the confirmed ROI and
native caption evidence.  This is a conservative single-body layout: it never
claims fine-grained semantic columns, but it keeps PaperWright's validators
green and preserves native text order.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .layout_models import FinalLayout, LayoutTask, NormalizedBBox
from .layout_review import validate_layout_review
from .models import Page

_STRONG_CAPTION_LINE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?\s*(?:[|.:])",
    re.IGNORECASE,
)
PROMPT_VERSION = "paperwright-layout-review-prompt-v0.4"
REVIEWER = "paperwright-routing-l0-rule"


def build_l0_final_layout(task: LayoutTask, page: Page) -> dict[str, Any]:
    """Build a conservative, validated visual-direct layout for one page."""

    roi_value = task.metadata.get("analysis_roi")
    if not isinstance(roi_value, Mapping) or not isinstance(
        roi_value.get("bbox"), Mapping
    ):
        raise ValueError("L0 fallback layout requires a confirmed Content ROI")
    roi = NormalizedBBox.from_dict(roi_value["bbox"]).to_dict()

    caption_boxes: list[dict[str, float]] = []
    for element in page.elements:
        text = element.text or ""
        if element.kind != "text" or not _STRONG_CAPTION_LINE.match(text):
            continue
        bbox = element.bbox
        caption_boxes.append(
            {
                "x": bbox.x / page.width,
                "y": bbox.y / page.height,
                "width": bbox.width / page.width,
                "height": bbox.height / page.height,
            }
        )

    regions: list[dict[str, Any]] = [
        {
            "region_id": "r-body",
            "bbox": dict(roi),
            "content_class": "text",
            "role": "body",
            "order": 1,
            "source_candidate_ids": [],
            "source_element_ids": [],
            "parent_region_id": None,
            "confidence": 0.8,
        }
    ]
    actions: list[dict[str, Any]] = [
        {
            "action_id": "a-add-body",
            "action": "add",
            "source_candidate_ids": [],
            "result_region_ids": ["r-body"],
            "bbox": dict(roi),
            "target_region_id": None,
            "reason": "L0 rule fallback body region from confirmed ROI",
        }
    ]

    if caption_boxes:
        x0 = min(item["x"] for item in caption_boxes)
        y0 = min(item["y"] for item in caption_boxes)
        x1 = max(item["x"] + item["width"] for item in caption_boxes)
        y1 = max(item["y"] + item["height"] for item in caption_boxes)
        pad = 0.006
        x0 = max(0.0, x0 - pad)
        y0 = max(0.0, y0 - pad)
        x1 = min(1.0, x1 + pad)
        y1 = min(1.0, y1 + pad)
        caption_bbox = {
            "x": x0,
            "y": y0,
            "width": x1 - x0,
            "height": y1 - y0,
        }
        regions.append(
            {
                "region_id": "r-caption",
                "bbox": caption_bbox,
                "content_class": "text",
                "role": "caption",
                "order": 2,
                "source_candidate_ids": [],
                "source_element_ids": [],
                "parent_region_id": None,
                "confidence": 0.9,
            }
        )
        actions.append(
            {
                "action_id": "a-add-caption",
                "action": "add",
                "source_candidate_ids": [],
                "result_region_ids": ["r-caption"],
                "bbox": caption_bbox,
                "target_region_id": None,
                "reason": "L0 caption separation from native Figure/Table marker",
            }
        )

    layout = {
        "contract_version": "paperwright-final-layout-v0.1",
        "source_sha256": task.source_sha256,
        "page": task.page.to_dict(),
        "reviewer": REVIEWER,
        "prompt_version": PROMPT_VERSION,
        "regions": regions,
        "actions": actions,
        "warnings": [],
    }
    final = FinalLayout.from_dict(layout)
    validate_layout_review(final, task)
    return layout


__all__ = ["build_l0_final_layout"]
