"""Low-resolution raster evidence for fast page-layout analysis.

The raster path deliberately treats native text boxes as a soft mask.  It
keeps the complete page-ink mask alongside the non-text residual so later
layout stages can recover figures containing labels, legends, or axes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import statistics
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .layout_models import NormalizedBBox
from .models import Element, Page

RASTER_LAYOUT_VERSION = "paperwright-raster-layout-v0.1"


@dataclass(frozen=True)
class RasterLayoutConfig:
    background_difference_threshold: int = 18
    text_padding_ratio: float = 0.0015
    grid_cell_px: int = 4
    grid_occupancy_threshold: int = 10
    bridge_cells: int = 3
    min_region_area_ratio: float = 0.0015
    min_region_width_ratio: float = 0.025
    min_region_height_ratio: float = 0.018
    min_residual_ink_pixels: int = 20

    def __post_init__(self) -> None:
        if not 1 <= self.background_difference_threshold <= 255:
            raise ValueError("background difference threshold must be in [1,255]")
        if not 0 <= self.text_padding_ratio <= 0.05:
            raise ValueError("text padding ratio must be in [0,0.05]")
        if self.grid_cell_px <= 0:
            raise ValueError("grid cell size must be positive")
        if not 1 <= self.grid_occupancy_threshold <= 255:
            raise ValueError("grid occupancy threshold must be in [1,255]")
        if self.bridge_cells < 0:
            raise ValueError("bridge cells cannot be negative")
        for value in (
            self.min_region_area_ratio,
            self.min_region_width_ratio,
            self.min_region_height_ratio,
        ):
            if not 0 <= value <= 1:
                raise ValueError("region ratios must be in [0,1]")
        if self.min_residual_ink_pixels < 1:
            raise ValueError("minimum residual ink pixels must be positive")


@dataclass(frozen=True)
class RasterVisualRegion:
    region_id: str
    bbox: NormalizedBBox
    page_area_ratio: float
    ink_coverage: float
    residual_coverage: float
    text_mask_coverage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "bbox": self.bbox.to_dict(),
            "page_area_ratio": self.page_area_ratio,
            "ink_coverage": self.ink_coverage,
            "residual_coverage": self.residual_coverage,
            "text_mask_coverage": self.text_mask_coverage,
        }


@dataclass(frozen=True)
class RasterPageAnalysis:
    page_index: int
    preview_width: int
    preview_height: int
    background_rgb: tuple[int, int, int]
    text_padding_px: int
    ink_coverage: float
    text_mask_coverage: float
    residual_coverage: float
    ink_mask_sha256: str
    text_mask_sha256: str
    residual_mask_sha256: str
    regions: tuple[RasterVisualRegion, ...]
    contract_version: str = RASTER_LAYOUT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "page_index": self.page_index,
            "preview": {
                "width": self.preview_width,
                "height": self.preview_height,
            },
            "background_rgb": list(self.background_rgb),
            "text_padding_px": self.text_padding_px,
            "coverage": {
                "ink": self.ink_coverage,
                "text_mask": self.text_mask_coverage,
                "residual": self.residual_coverage,
            },
            "mask_sha256": {
                "ink": self.ink_mask_sha256,
                "text": self.text_mask_sha256,
                "residual": self.residual_mask_sha256,
            },
            "regions": [item.to_dict() for item in self.regions],
        }

    def canonical_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )


@dataclass(frozen=True)
class RasterPageResult:
    analysis: RasterPageAnalysis
    ink_mask: Image.Image
    text_mask: Image.Image
    residual_mask: Image.Image


def _mask_sha256(mask: Image.Image) -> str:
    normalized = mask.convert("L")
    payload = (
        normalized.width.to_bytes(4, "big")
        + normalized.height.to_bytes(4, "big")
        + normalized.tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


def _coverage(mask: Image.Image) -> float:
    histogram = mask.convert("L").histogram()
    occupied = sum(count for value, count in enumerate(histogram) if value > 0)
    return occupied / max(mask.width * mask.height, 1)


def _crop_coverage(mask: Image.Image, box: tuple[int, int, int, int]) -> float:
    return _coverage(mask.crop(box))


def _background_rgb(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    stride = max(1, min(width, height) // 128)
    samples: list[tuple[int, int, int]] = []
    for x in range(0, width, stride):
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, height - 1)))
    for y in range(stride, max(stride, height - stride), stride):
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((width - 1, y)))
    if not samples:
        return (255, 255, 255)
    return tuple(
        int(statistics.median(value[channel] for value in samples))
        for channel in range(3)
    )


def _ink_mask(
    preview: Image.Image,
    background: tuple[int, int, int],
    threshold: int,
) -> Image.Image:
    rgb = preview.convert("RGB")
    difference = ImageChops.difference(
        rgb,
        Image.new("RGB", rgb.size, background),
    )
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return maximum.point(lambda value: 255 if value >= threshold else 0, "L")


def _text_mask(
    page: Page,
    preview_size: tuple[int, int],
    *,
    padding_px: int,
) -> Image.Image:
    width, height = preview_size
    mask = Image.new("L", preview_size, 0)
    draw = ImageDraw.Draw(mask)
    for element in page.elements:
        if element.kind != "text" or not element.text:
            continue
        left = math.floor(element.bbox.x / page.width * width) - padding_px
        top = math.floor(element.bbox.y / page.height * height) - padding_px
        right = math.ceil(element.bbox.right / page.width * width) + padding_px
        bottom = math.ceil(element.bbox.bottom / page.height * height) + padding_px
        left = max(0, min(left, width - 1))
        top = max(0, min(top, height - 1))
        right = max(left, min(right, width - 1))
        bottom = max(top, min(bottom, height - 1))
        draw.rectangle((left, top, right, bottom), fill=255)
    return mask


def _grid_mask(mask: Image.Image, config: RasterLayoutConfig) -> Image.Image:
    grid_width = max(1, math.ceil(mask.width / config.grid_cell_px))
    grid_height = max(1, math.ceil(mask.height / config.grid_cell_px))
    density = mask.resize((grid_width, grid_height), Image.Resampling.BOX)
    occupied = density.point(
        lambda value: 255 if value >= config.grid_occupancy_threshold else 0,
        "L",
    )
    if config.bridge_cells:
        kernel = config.bridge_cells * 2 + 1
        occupied = occupied.filter(ImageFilter.MaxFilter(kernel))
    return occupied


def _component_boxes(mask: Image.Image) -> Iterable[tuple[int, int, int, int]]:
    width, height = mask.size
    pixels = mask.load()
    visited = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if visited[offset] or not pixels[x, y]:
                continue
            visited[offset] = 1
            stack = [(x, y)]
            left = right = x
            top = bottom = y
            while stack:
                current_x, current_y = stack.pop()
                left = min(left, current_x)
                right = max(right, current_x)
                top = min(top, current_y)
                bottom = max(bottom, current_y)
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    next_offset = next_y * width + next_x
                    if visited[next_offset] or not pixels[next_x, next_y]:
                        continue
                    visited[next_offset] = 1
                    stack.append((next_x, next_y))
            yield left, top, right + 1, bottom + 1


def _visual_regions(
    ink: Image.Image,
    text: Image.Image,
    residual: Image.Image,
    config: RasterLayoutConfig,
) -> tuple[RasterVisualRegion, ...]:
    grid = _grid_mask(residual, config)
    scale_x = residual.width / grid.width
    scale_y = residual.height / grid.height
    candidates: list[tuple[int, int, int, int]] = []
    for left, top, right, bottom in _component_boxes(grid):
        pixel_box = (
            max(0, math.floor(left * scale_x)),
            max(0, math.floor(top * scale_y)),
            min(residual.width, math.ceil(right * scale_x)),
            min(residual.height, math.ceil(bottom * scale_y)),
        )
        width = pixel_box[2] - pixel_box[0]
        height = pixel_box[3] - pixel_box[1]
        area_ratio = width * height / (residual.width * residual.height)
        if area_ratio < config.min_region_area_ratio:
            continue
        if width / residual.width < config.min_region_width_ratio:
            continue
        if height / residual.height < config.min_region_height_ratio:
            continue
        residual_crop = residual.crop(pixel_box)
        residual_pixels = sum(
            count
            for value, count in enumerate(residual_crop.histogram())
            if value > 0
        )
        if residual_pixels < config.min_residual_ink_pixels:
            continue
        candidates.append(pixel_box)

    regions: list[RasterVisualRegion] = []
    for index, box in enumerate(sorted(candidates, key=lambda item: (item[1], item[0]))):
        left, top, right, bottom = box
        normalized = NormalizedBBox(
            x=left / residual.width,
            y=top / residual.height,
            width=(right - left) / residual.width,
            height=(bottom - top) / residual.height,
        )
        regions.append(
            RasterVisualRegion(
                region_id=f"RV{index + 1:04d}",
                bbox=normalized,
                page_area_ratio=normalized.width * normalized.height,
                ink_coverage=_crop_coverage(ink, box),
                residual_coverage=_crop_coverage(residual, box),
                text_mask_coverage=_crop_coverage(text, box),
            )
        )
    return tuple(regions)


def analyze_page_raster(
    preview: Image.Image,
    page: Page,
    *,
    config: RasterLayoutConfig | None = None,
) -> RasterPageResult:
    """Return deterministic page, text, and non-text masks plus candidates."""

    settings = config or RasterLayoutConfig()
    if preview.width <= 0 or preview.height <= 0:
        raise ValueError("preview dimensions must be positive")
    background = _background_rgb(preview)
    ink = _ink_mask(
        preview,
        background,
        settings.background_difference_threshold,
    )
    padding = max(
        1,
        round(min(preview.size) * settings.text_padding_ratio),
    )
    text = _text_mask(page, preview.size, padding_px=padding)
    residual = ImageChops.subtract(ink, text)
    regions = _visual_regions(ink, text, residual, settings)
    analysis = RasterPageAnalysis(
        page_index=page.page_index,
        preview_width=preview.width,
        preview_height=preview.height,
        background_rgb=background,
        text_padding_px=padding,
        ink_coverage=_coverage(ink),
        text_mask_coverage=_coverage(text),
        residual_coverage=_coverage(residual),
        ink_mask_sha256=_mask_sha256(ink),
        text_mask_sha256=_mask_sha256(text),
        residual_mask_sha256=_mask_sha256(residual),
        regions=regions,
    )
    return RasterPageResult(analysis, ink, text, residual)


def render_raster_overlay(
    preview: Image.Image,
    analysis: RasterPageAnalysis,
) -> Image.Image:
    """Draw deterministic raster-candidate boxes for human inspection."""

    overlay = preview.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    line_width = max(2, round(min(overlay.size) / 350))
    for region in analysis.regions:
        box = region.bbox.to_pixel_box(
            image_width=overlay.width,
            image_height=overlay.height,
        )
        draw.rectangle(box, outline=(255, 48, 48), width=line_width)
        label_origin = (box[0] + line_width, box[1] + line_width)
        label_box = draw.textbbox(label_origin, region.region_id)
        draw.rectangle(label_box, fill=(255, 255, 255))
        draw.text(label_origin, region.region_id, fill=(210, 20, 20))
    return overlay
