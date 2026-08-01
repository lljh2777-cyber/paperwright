"""Deterministic export helpers for hybrid-layout review tasks."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .exceptions import OutputConflictError
from .layout_models import LayoutTask, NormalizedBBox
from .layout_review import write_layout_review_instructions

_CANDIDATE_COLORS = {
    "text": (32, 117, 255),
    "image": (38, 166, 91),
    "vector": (145, 80, 210),
    "raster": (220, 45, 55),
    "mixed": (230, 74, 25),
    "unknown": (96, 96, 96),
}
_SEPARATOR_COLOR = (255, 166, 0)
_ROI_COLOR = (255, 48, 48)


def _task_content_roi(task: LayoutTask) -> NormalizedBBox | None:
    value = task.metadata.get("analysis_roi")
    if not isinstance(value, dict) or not isinstance(value.get("bbox"), dict):
        return None
    return NormalizedBBox.from_dict(value["bbox"])


def render_content_roi_overlay(
    preview: Image.Image,
    task: LayoutTask,
) -> Image.Image:
    """Darken the excluded perimeter and outline the analysis ROI."""

    base = preview.convert("RGBA")
    roi = _task_content_roi(task)
    if roi is None:
        return base.convert("RGB")
    left, top, right, bottom = roi.to_pixel_box(
        image_width=base.width,
        image_height=base.height,
    )
    shade = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    fill = (20, 20, 20, 112)
    if top > 0:
        draw.rectangle((0, 0, base.width - 1, top - 1), fill=fill)
    if bottom < base.height - 1:
        draw.rectangle(
            (0, bottom + 1, base.width - 1, base.height - 1),
            fill=fill,
        )
    if left > 0:
        draw.rectangle((0, top, left - 1, bottom), fill=fill)
    if right < base.width - 1:
        draw.rectangle(
            (right + 1, top, base.width - 1, bottom),
            fill=fill,
        )
    composed = Image.alpha_composite(base, shade)
    outline = ImageDraw.Draw(composed)
    width = max(2, round(min(base.size) / 300))
    outline.rectangle((left, top, right, bottom), outline=_ROI_COLOR, width=width)
    return composed.convert("RGB")


def _candidate_color(kinds: tuple[str, ...]) -> tuple[int, int, int]:
    relevant = tuple(
        item
        for item in kinds
        if item in {"text", "image", "vector", "raster"}
    )
    if len(set(relevant)) > 1:
        return _CANDIDATE_COLORS["mixed"]
    if relevant:
        return _CANDIDATE_COLORS[relevant[0]]
    return _CANDIDATE_COLORS["unknown"]


def render_layout_overlay(
    preview: Image.Image,
    task: LayoutTask,
) -> Image.Image:
    """Return a labeled overlay without modifying the source preview."""

    overlay = render_content_roi_overlay(preview, task)
    draw = ImageDraw.Draw(overlay)
    width = max(1, round(min(overlay.size) / 350))
    for candidate in sorted(task.candidates, key=lambda item: item.candidate_id):
        box = candidate.bbox.to_pixel_box(
            image_width=overlay.width,
            image_height=overlay.height,
        )
        color = _candidate_color(candidate.element_kinds)
        draw.rectangle(box, outline=color, width=width)
        label_x, label_y = box[0] + width, box[1] + width
        text_box = draw.textbbox((label_x, label_y), candidate.candidate_id)
        draw.rectangle(text_box, fill=(255, 255, 255))
        draw.text((label_x, label_y), candidate.candidate_id, fill=color)

    for separator in sorted(task.separators, key=lambda item: item.separator_id):
        box = separator.bbox.to_pixel_box(
            image_width=overlay.width,
            image_height=overlay.height,
        )
        draw.rectangle(box, outline=_SEPARATOR_COLOR, width=width)
        label_x, label_y = box[0] + width, box[1] + width
        text_box = draw.textbbox((label_x, label_y), separator.separator_id)
        draw.rectangle(text_box, fill=(255, 255, 255))
        draw.text((label_x, label_y), separator.separator_id, fill=_SEPARATOR_COLOR)
    return overlay


def export_layout_task_bundle(
    output_dir: str | Path,
    task: LayoutTask,
    preview: Image.Image,
) -> Path:
    """Write task JSON, preview, and overlay into a new directory.

    Existing directories are rejected so review evidence is never overwritten.
    """

    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise OutputConflictError(f"布局任务目录已存在，拒绝覆盖: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    (destination / "layout-task.json").write_text(
        task.canonical_json(),
        encoding="utf-8",
        newline="\n",
    )
    preview.convert("RGB").save(
        destination / task.preview_filename,
        format="PNG",
        optimize=False,
        compress_level=9,
    )
    render_content_roi_overlay(preview, task).save(
        destination / "content-roi.png",
        format="PNG",
        optimize=False,
        compress_level=9,
    )
    render_layout_overlay(preview, task).save(
        destination / task.overlay_filename,
        format="PNG",
        optimize=False,
        compress_level=9,
    )
    write_layout_review_instructions(
        destination / "review-instructions.md",
        task,
    )
    return destination
