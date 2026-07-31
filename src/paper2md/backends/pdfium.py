"""Thin pypdfium2 adapter for born-digital PDFs.

This module delegates parsing, font decoding, image decoding, and rendering to
PDFium.  It only normalizes native page objects into PhysicalDocument v0.2.
"""

from __future__ import annotations

import ctypes
from collections import Counter
import hashlib
import importlib.metadata
import io
import math
from pathlib import Path
import statistics
from typing import Any
import unicodedata

from PIL import ImageStat

from ..config import Paper2MDConfig
from ..exceptions import (
    BackendExecutionError,
    BackendUnavailableError,
    CorruptInputError,
)
from ..models import BBox, Element, Page, PhysicalDocument, Provenance
from ..region_render import RegionRenderRequest, RegionRenderResult
from .base import (
    BackendCapabilities,
    BackendIdentity,
    BackendResult,
    ExtractedAsset,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdfium_runtime() -> tuple[Any, BackendIdentity]:
    try:
        import pypdfium2 as pdfium
        import pypdfium2_raw
    except ImportError as exc:
        raise BackendUnavailableError(
            "PDFium 后端需要锁定依赖 pypdfium2==5.3.0"
        ) from exc
    wrapper = importlib.metadata.version("pypdfium2")
    engine = str(pdfium.PDFIUM_INFO)
    package_dir = Path(pypdfium2_raw.__file__).resolve().parent
    candidates = sorted(
        path
        for path in package_dir.iterdir()
        if path.is_file()
        and (
            path.name.startswith("libpdfium.")
            or path.name.casefold() == "pdfium.dll"
        )
    )
    binary_hash = _sha256(candidates[0]) if len(candidates) == 1 else None
    return pdfium, BackendIdentity("pdfium", wrapper, engine, binary_hash)


def _clamped_bbox(
    bounds: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> BBox | None:
    left, bottom, right, top = (float(value) for value in bounds)
    x0 = min(max(left, 0.0), page_width)
    x1 = min(max(right, 0.0), page_width)
    y0 = min(max(page_height - top, 0.0), page_height)
    y1 = min(max(page_height - bottom, 0.0), page_height)
    if x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
        return None
    return BBox(x0, y0, x1 - x0, y1 - y0)


def _degenerate_bbox_reason(
    bounds: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> str:
    left, bottom, right, top = (float(value) for value in bounds)
    raw_width = right - left
    raw_height = top - bottom
    if raw_width <= 1e-6 and raw_height <= 1e-6:
        return "zero_area"
    if raw_width <= 1e-6:
        return "zero_width"
    if raw_height <= 1e-6:
        return "zero_height"
    if right <= 0 or left >= page_width or top <= 0 or bottom >= page_height:
        return "outside_page"
    return "collapsed_after_page_clamp"


def _degenerate_text_class(text: str | None, *, extraction_failed: bool) -> str:
    if extraction_failed:
        return "unreadable_text"
    if not text:
        return "empty_text"
    if not text.strip():
        return "whitespace_text"
    return "nonempty_text"


def _restore_missing_spaces_from_charboxes(
    characters: list[tuple[str, tuple[float, float, float, float]]],
) -> tuple[str, int]:
    """Restore omitted spaces using only native character geometry.

    Some PDFs encode visually separated words in one text object without a
    Unicode space.  Insert a space only between two alphanumeric characters
    whose horizontal gap is large relative to both glyph heights.
    """

    characters = [
        ("\u0002" if character in {"\ufffe", "\uffff"} else character, box)
        for character, box in characters
    ]
    pair_gaps: list[float] = []
    previous: tuple[str, tuple[float, float, float, float]] | None = None
    for character, box in characters:
        if (
            not character
            or character.isspace()
            or unicodedata.category(character) == "Cc"
        ):
            previous = None
            continue
        if previous is not None:
            previous_character, previous_box = previous
            gap = box[0] - previous_box[2]
            if (
                previous_character.isalpha()
                and character.isalpha()
                and gap >= 0
            ):
                pair_gaps.append(gap)
        previous = (character, box)
    typical_gap = statistics.median(pair_gaps) if pair_gaps else 0.0

    output: list[str] = []
    previous = None
    inserted = 0
    for character, box in characters:
        if not character:
            continue
        if character.isspace() or unicodedata.category(character) == "Cc":
            output.append(character)
            previous = None
            continue
        if previous is not None:
            previous_character, previous_box = previous
            left, bottom, _, top = box
            _, previous_bottom, previous_right, previous_top = previous_box
            height = max(0.0, top - bottom)
            previous_height = max(0.0, previous_top - previous_bottom)
            vertical_overlap = min(top, previous_top) - max(
                bottom,
                previous_bottom,
            )
            threshold = max(1.20, min(height, previous_height) * 0.20)
            threshold = max(threshold, typical_gap * 2.4)
            gap = left - previous_right
            if (
                previous_character.isalpha()
                and character.isalpha()
                and vertical_overlap
                >= min(height, previous_height) * 0.50
                and gap > threshold
            ):
                output.append(" ")
                inserted += 1
        output.append(character)
        previous = (character, box)
    return "".join(output), inserted


def _object_address(raw: Any) -> int:
    return int(ctypes.cast(raw, ctypes.c_void_p).value or 0)


def _character_runs_by_object(
    textpage: Any,
) -> dict[int, list[tuple[str, tuple[float, float, float, float]]]]:
    runs: dict[
        int,
        list[tuple[str, tuple[float, float, float, float]]],
    ] = {}
    for index in range(textpage.count_chars()):
        text_object = textpage.get_textobj(index)
        if text_object is None:
            continue
        character = textpage.get_text_range(index, 1)
        if not character:
            continue
        try:
            box = tuple(float(value) for value in textpage.get_charbox(index))
        except Exception:
            continue
        runs.setdefault(_object_address(text_object.raw), []).append(
            (character, box)
        )
    return runs


def _reading_order(elements: list[Element], page_width: float) -> list[Element]:
    """Deterministic basic column order without semantic paragraph inference."""

    text = [item for item in elements if item.kind == "text"]
    others = [item for item in elements if item.kind != "text"]
    if len(text) < 2:
        return sorted(text, key=lambda item: (item.bbox.y, item.bbox.x)) + others

    # A native text object can be a whole line or only one word.  Classifying
    # word fragments as columns makes a single line read as "all left words,
    # then all right words".  First form conservative same-line groups.  The
    # gap limit intentionally remains much smaller than a normal column gutter.
    line_groups: list[list[Element]] = []
    for item in sorted(text, key=lambda value: (value.bbox.y, value.bbox.x)):
        match: list[Element] | None = None
        item_center = item.bbox.y + item.bbox.height / 2
        for group in reversed(line_groups[-12:]):
            group_top = min(value.bbox.y for value in group)
            group_bottom = max(value.bbox.y + value.bbox.height for value in group)
            group_center = (group_top + group_bottom) / 2
            group_height = group_bottom - group_top
            center_limit = max(2.5, min(item.bbox.height, group_height) * 0.45)
            if abs(item_center - group_center) > center_limit:
                continue
            group_left = min(value.bbox.x for value in group)
            group_right = max(value.bbox.x + value.bbox.width for value in group)
            horizontal_gap = max(
                group_left - (item.bbox.x + item.bbox.width),
                item.bbox.x - group_right,
                0.0,
            )
            gap_limit = max(
                8.0,
                min(14.0, max(item.bbox.height, group_height) * 1.2),
            )
            if horizontal_gap <= gap_limit:
                match = group
                break
        if match is None:
            line_groups.append([item])
        else:
            match.append(item)

    # Native objects from one visual line may arrive in an order that causes
    # the first pass to create two groups (for example, a later suffix is seen
    # before the fragment that bridges it to the prefix).  Reconsider all
    # co-linear groups until reaching a fixed point.  The ordinary threshold
    # accepts a normal word space; consecutive native object order expands it
    # slightly, but never enough to bridge a real column gutter.
    def group_bounds(
        group: list[Element],
    ) -> tuple[float, float, float, float]:
        return (
            min(item.bbox.x for item in group),
            min(item.bbox.y for item in group),
            max(item.bbox.right for item in group),
            max(item.bbox.bottom for item in group),
        )

    def native_range(group: list[Element]) -> tuple[int, int] | None:
        values = [
            value
            for item in group
            if isinstance((value := item.metadata.get("native_order")), int)
        ]
        return (min(values), max(values)) if values else None

    def groups_can_merge(left_group: list[Element], right_group: list[Element]) -> bool:
        left, left_top, left_right, left_bottom = group_bounds(left_group)
        right, right_top, right_right, right_bottom = group_bounds(right_group)
        overlap = min(left_bottom, right_bottom) - max(left_top, right_top)
        smaller_height = min(
            left_bottom - left_top,
            right_bottom - right_top,
        )
        if smaller_height <= 0 or overlap < smaller_height * 0.45:
            return False
        horizontal_gap = max(
            left - right_right,
            right - left_right,
            0.0,
        )
        gap_limit = max(6.0, min(10.0, smaller_height * 0.8))

        if left <= right:
            earlier, later = native_range(left_group), native_range(right_group)
        else:
            earlier, later = native_range(right_group), native_range(left_group)
        native_contiguous = (
            earlier is not None
            and later is not None
            and earlier[1] + 1 == later[0]
        )
        if native_contiguous:
            gap_limit = max(gap_limit, min(14.0, smaller_height * 1.2))
        return horizontal_gap <= gap_limit

    merged_groups = [list(group) for group in line_groups]
    while True:
        merged_groups.sort(
            key=lambda values: (
                min(item.bbox.y for item in values),
                min(item.bbox.x for item in values),
            )
        )
        merged = False
        for left_index, left_group in enumerate(merged_groups):
            for right_index in range(left_index + 1, len(merged_groups)):
                right_group = merged_groups[right_index]
                if min(item.bbox.y for item in right_group) > max(
                    item.bbox.bottom for item in left_group
                ) + 2.5:
                    break
                if groups_can_merge(left_group, right_group):
                    left_group.extend(right_group)
                    del merged_groups[right_index]
                    merged = True
                    break
            if merged:
                break
        if not merged:
            break
    line_groups = merged_groups

    def line_bbox(group: list[Element]) -> tuple[float, float, float, float]:
        left = min(item.bbox.x for item in group)
        top = min(item.bbox.y for item in group)
        right = max(item.bbox.x + item.bbox.width for item in group)
        bottom = max(item.bbox.y + item.bbox.height for item in group)
        return left, top, right, bottom

    wide = [
        group
        for group in line_groups
        if line_bbox(group)[2] - line_bbox(group)[0] >= page_width * 0.65
    ]
    narrow = [group for group in line_groups if group not in wide]
    anchors = sorted(wide, key=lambda group: (line_bbox(group)[1], line_bbox(group)[0]))
    ordered_groups: list[list[Element]] = []
    lower = -1.0
    for anchor in anchors + [None]:
        upper = line_bbox(anchor)[1] if anchor is not None else float("inf")
        band = [group for group in narrow if lower < line_bbox(group)[1] < upper]
        left = [group for group in band if line_bbox(group)[0] < page_width / 2]
        right = [group for group in band if line_bbox(group)[0] >= page_width / 2]
        if left and right:
            ordered_groups.extend(
                sorted(left, key=lambda group: (line_bbox(group)[1], line_bbox(group)[0]))
            )
            ordered_groups.extend(
                sorted(right, key=lambda group: (line_bbox(group)[1], line_bbox(group)[0]))
            )
        else:
            ordered_groups.extend(
                sorted(band, key=lambda group: (line_bbox(group)[1], line_bbox(group)[0]))
            )
        if anchor is not None:
            ordered_groups.append(anchor)
            lower = line_bbox(anchor)[1]

    result: list[Element] = []
    for line_number, group in enumerate(ordered_groups):
        for line_position, item in enumerate(
            sorted(group, key=lambda value: (value.bbox.x, value.bbox.y))
        ):
            result.append(
                Element(
                    element_id=item.element_id,
                    kind=item.kind,
                    page_index=item.page_index,
                    bbox=item.bbox,
                    provenance=item.provenance,
                    text=item.text,
                    source_object_id=item.source_object_id,
                    metadata={
                        **item.metadata,
                        "line_group": line_number,
                        "line_position": line_position,
                    },
                )
            )
    # Non-text objects are kept after text, ordered geometrically. Markdown
    # placement is page-local and explicitly disclosed rather than inferred.
    result.extend(sorted(others, key=lambda item: (item.bbox.y, item.bbox.x)))
    return result


class PDFiumBackend:
    capabilities = BackendCapabilities(
        text_runs=True,
        images=True,
        vectors=True,
        links=False,
        render=True,
    )

    def __init__(self) -> None:
        self._pdfium, self.identity = _pdfium_runtime()

    def extract(self, source: Path, config: Paper2MDConfig) -> BackendResult:
        pdfium = self._pdfium
        source_hash = _sha256(source)
        assets: list[ExtractedAsset] = []
        warnings: list[dict[str, object]] = []
        pages: list[Page] = []
        degenerate_counts: Counter[str] = Counter()
        degenerate_pages: list[dict[str, object]] = []
        try:
            document = pdfium.PdfDocument(source)
        except Exception as exc:
            raise CorruptInputError(f"PDFium 无法打开 PDF: {exc}") from exc

        try:
            if len(document) > config.limits.max_pages:
                raise BackendExecutionError(
                    f"页数 {len(document)} 超过限制 {config.limits.max_pages}"
                )
            metadata = {
                key: value
                for key, value in document.get_metadata_dict().items()
                if value
            }
            for page_index in range(len(document)):
                page = document[page_index]
                try:
                    pages.append(
                        self._extract_page(
                            page,
                            page_index,
                            assets,
                            warnings,
                            degenerate_counts,
                            degenerate_pages,
                        )
                    )
                finally:
                    page.close()
        except BackendExecutionError:
            raise
        except Exception as exc:
            raise BackendExecutionError(f"PDFium 提取失败: {exc}") from exc
        finally:
            document.close()

        physical = PhysicalDocument(
            source_sha256=source_hash,
            backend=self.identity.name,
            backend_version=(
                f"pypdfium2/{self.identity.wrapper_version} "
                f"pdfium/{self.identity.engine_version}"
            ),
            pages=tuple(pages),
            metadata={
                "pdf_metadata": metadata,
                "coordinate_mapping": (
                    "PDFium bottom-left PDF points converted to "
                    "top-left/pdf-point/y-down"
                ),
                "source_object_identity": "unavailable_from_public_wrapper",
                "text_order": "deterministic_basic_columns_v2_iterative_line_merge",
                "text_object_extraction": (
                    "pdfium_native_text_object_character_geometry_v3"
                ),
                "text_line_reconstruction": "native_object_geometry_v2",
                "degenerate_object_handling": {
                    "policy_version": "paper2md-degenerate-object-policy-v1",
                    "counts": dict(sorted(degenerate_counts.items())),
                    "pages": degenerate_pages,
                },
            },
        )
        return BackendResult(physical, tuple(assets), tuple(warnings))

    def render_page_preview(
        self,
        source: Path,
        page_index: int,
        *,
        scale: float = 1.5,
        max_pixels: int = 16_000_000,
    ) -> Any:
        """Render one complete page for layout review, without OCR."""

        if not math.isfinite(scale) or scale <= 0:
            raise BackendExecutionError("layout preview scale 必须是正有限数")
        if max_pixels <= 0:
            raise BackendExecutionError("layout preview max_pixels 必须为正")
        document = self._pdfium.PdfDocument(source)
        try:
            if not 0 <= page_index < len(document):
                raise BackendExecutionError("layout preview 页码越界")
            page = document[page_index]
            try:
                width_px = int(math.ceil(float(page.get_width()) * scale))
                height_px = int(math.ceil(float(page.get_height()) * scale))
                if width_px * height_px > max_pixels:
                    raise BackendExecutionError("layout preview 超过像素上限")
                bitmap = page.render(
                    scale=scale,
                    rotation=0,
                    may_draw_forms=False,
                    draw_annots=False,
                    rev_byteorder=True,
                )
                try:
                    return bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
        except BackendExecutionError:
            raise
        except Exception as exc:
            raise BackendExecutionError(
                f"PDFium layout preview 失败: {exc}"
            ) from exc
        finally:
            document.close()

    def render_region(
        self,
        source: Path,
        request: RegionRenderRequest,
        *,
        expected_source_sha256: str,
    ) -> RegionRenderResult:
        """Render one real clipped page region through PDFium.

        ``crop`` is expressed as amounts cut from the rendered page's
        left/bottom/right/top edges.  Paper2MD's public bbox is top-left,
        y-down, so the bottom crop is ``page_height - bbox.bottom``.
        """

        source_hash = _sha256(source)
        if source_hash != expected_source_sha256:
            raise BackendExecutionError("region render 输入 PDF 哈希与提取结果不一致")
        pdfium = self._pdfium
        document = pdfium.PdfDocument(source)
        try:
            if not 0 <= request.page_index < len(document):
                raise BackendExecutionError("region render 页码越界")
            page = document[request.page_index]
            try:
                width = float(page.get_width())
                height = float(page.get_height())
                bbox = request.bbox
                if (
                    bbox.x < 0
                    or bbox.y < 0
                    or bbox.right > width + 1e-6
                    or bbox.bottom > height + 1e-6
                ):
                    raise BackendExecutionError("region render bbox 越界")
                if bbox.bottom > request.caption_top - request.caption_guard + 1e-6:
                    raise BackendExecutionError("region render bbox 侵入 caption guard")
                area_ratio = bbox.width * bbox.height / (width * height)
                if area_ratio >= request.max_page_area_ratio:
                    raise BackendExecutionError("region render 拒绝整页或近整页截图")
                width_px = int(math.ceil(bbox.width * request.scale))
                height_px = int(math.ceil(bbox.height * request.scale))
                if width_px <= 0 or height_px <= 0:
                    raise BackendExecutionError("region render 像素尺寸为零")
                if width_px * height_px > request.max_pixels:
                    raise BackendExecutionError("region render 超过像素上限")
                crop = (
                    bbox.x,
                    height - bbox.bottom,
                    width - bbox.right,
                    bbox.y,
                )
                bitmap = page.render(
                    scale=request.scale,
                    rotation=0,
                    crop=crop,
                    may_draw_forms=False,
                    draw_annots=False,
                    rev_byteorder=True,
                )
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                if image.width * image.height > request.max_pixels:
                    raise BackendExecutionError("region render 实际像素数超过上限")
                channel_variance = ImageStat.Stat(image).var
                pixel_variance = float(sum(channel_variance) / len(channel_variance))
                if not math.isfinite(pixel_variance) or pixel_variance < request.min_variance:
                    raise BackendExecutionError("region render 空白或近恒定图像")
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=False, compress_level=9)
                data = buffer.getvalue()
                return RegionRenderResult(
                    figure_id=request.figure_id,
                    data=data,
                    width_px=image.width,
                    height_px=image.height,
                    sha256=hashlib.sha256(data).hexdigest(),
                    pixel_variance=pixel_variance,
                    page_area_ratio=area_ratio,
                    page_rotation=int(page.get_rotation()),
                    renderer_version=(
                        f"pypdfium2/{self.identity.wrapper_version} "
                        f"pdfium/{self.identity.engine_version}"
                    ),
                    source_sha256=source_hash,
                    bbox=bbox,
                    scale=request.scale,
                    dpi=request.dpi,
                )
            finally:
                page.close()
        except BackendExecutionError:
            raise
        except Exception as exc:
            raise BackendExecutionError(f"PDFium region render 失败: {exc}") from exc
        finally:
            document.close()

    def _extract_page(
        self,
        page: Any,
        page_index: int,
        assets: list[ExtractedAsset],
        warnings: list[dict[str, object]],
        degenerate_counts: Counter[str],
        degenerate_pages: list[dict[str, object]],
    ) -> Page:
        pdfium = self._pdfium
        width, height = float(page.get_width()), float(page.get_height())
        rotation = int(page.get_rotation())
        textpage = page.get_textpage()
        character_runs = _character_runs_by_object(textpage)
        elements: list[Element] = []
        image_index = 0
        vector_index = 0
        page_degenerate_counts: Counter[str] = Counter()
        try:
            for raw_index, obj in enumerate(page.get_objects()):
                bounds = obj.get_bounds()
                bbox = _clamped_bbox(bounds, width, height)
                if bbox is None:
                    reason = _degenerate_bbox_reason(bounds, width, height)
                    if isinstance(obj, pdfium.PdfTextObj):
                        text: str | None = None
                        extraction_failed = False
                        try:
                            obj.textpage = textpage
                            text = obj.extract()
                        except Exception:
                            extraction_failed = True
                        text_class = _degenerate_text_class(
                            text,
                            extraction_failed=extraction_failed,
                        )
                        diagnostic_code = f"ignored_degenerate_{text_class}"
                        if text_class in {"unreadable_text", "nonempty_text"}:
                            diagnostic_code = f"unplaced_degenerate_{text_class}"
                            warning: dict[str, object] = {
                                "code": diagnostic_code,
                                "page": page_index + 1,
                                "raw_object_index": raw_index,
                                "bbox_reason": reason,
                            }
                            if text:
                                warning.update(
                                    {
                                        "text_sha256": hashlib.sha256(
                                            text.encode("utf-8")
                                        ).hexdigest(),
                                        "snippet": " ".join(text.split())[:160],
                                    }
                                )
                            warnings.append(warning)
                    elif getattr(obj, "type", None) == 5:
                        # Form XObjects are structural containers. PDFium's
                        # recursive iterator yields their children separately.
                        diagnostic_code = "ignored_degenerate_form_container"
                    elif getattr(obj, "type", None) == 2:
                        diagnostic_code = "unplaced_degenerate_vector_path"
                        warnings.append(
                            {
                                "code": diagnostic_code,
                                "page": page_index + 1,
                                "raw_object_index": raw_index,
                                "bbox_reason": reason,
                            }
                        )
                    elif isinstance(obj, pdfium.PdfImage):
                        diagnostic_code = "unplaced_degenerate_image"
                        warnings.append(
                            {
                                "code": diagnostic_code,
                                "page": page_index + 1,
                                "raw_object_index": raw_index,
                                "bbox_reason": reason,
                            }
                        )
                    else:
                        diagnostic_code = "unplaced_degenerate_unsupported_object"
                        warnings.append(
                            {
                                "code": diagnostic_code,
                                "page": page_index + 1,
                                "raw_object_index": raw_index,
                                "object_type": getattr(obj, "type", None),
                                "bbox_reason": reason,
                            }
                        )
                    degenerate_counts[diagnostic_code] += 1
                    page_degenerate_counts[diagnostic_code] += 1
                    continue
                source_ref = f"page:{page_index}:native-object-index:{raw_index}"
                if isinstance(obj, pdfium.PdfTextObj):
                    # A bounded query returns every glyph touching the
                    # rectangle.  On tight scientific layouts this leaks
                    # descenders and superscripts from neighbouring lines.
                    # Extract the native object's own text instead.
                    try:
                        obj.textpage = textpage
                        text = obj.extract().strip()
                        geometry_spaces_inserted = 0
                        character_run = character_runs.get(
                            _object_address(obj.raw)
                        )
                        if character_run:
                            reconstructed, geometry_spaces_inserted = (
                                _restore_missing_spaces_from_charboxes(
                                    character_run
                                )
                            )
                            if reconstructed.strip():
                                text = reconstructed.strip()
                        extraction_method = (
                            "native_text_object_character_geometry"
                        )
                    except Exception:
                        text = textpage.get_text_bounded(*obj.get_bounds()).strip()
                        extraction_method = "native_text_object_bounded_text"
                        geometry_spaces_inserted = 0
                        warnings.append(
                            {
                                "code": "native_text_object_extract_fallback_bounded",
                                "page": page_index + 1,
                                "raw_object_index": raw_index,
                            }
                        )
                    if not text:
                        continue
                    try:
                        font_name = obj.get_font().get_base_name()
                    except Exception:
                        font_name = None
                    elements.append(
                        Element(
                            element_id=f"p{page_index:04d}-text-{raw_index:05d}",
                            kind="text",
                            page_index=page_index,
                            bbox=bbox,
                            text=text,
                            source_object_id=None,
                            provenance=Provenance(
                                backend="pdfium",
                                method=extraction_method,
                                source_ref=source_ref,
                                confidence=1.0,
                                unavailable_reason=(
                                    "stable native PDF object ID unavailable"
                                ),
                            ),
                            metadata={
                                "font_name": font_name,
                                "font_size": float(obj.get_font_size()),
                                "raw_object_index": raw_index,
                                "native_order": raw_index,
                                "geometry_spaces_inserted": (
                                    geometry_spaces_inserted
                                ),
                            },
                        )
                    )
                elif isinstance(obj, pdfium.PdfImage):
                    element_id = f"p{page_index:04d}-image-{image_index:04d}"
                    image_index += 1
                    bitmap = obj.get_bitmap(render=True, scale_to_original=True)
                    try:
                        pil_image = bitmap.to_pil().convert("RGB")
                        buffer = io.BytesIO()
                        pil_image.save(
                            buffer,
                            format="PNG",
                            optimize=False,
                            compress_level=9,
                        )
                        image_bytes = buffer.getvalue()
                        width_px, height_px = pil_image.size
                    finally:
                        bitmap.close()
                    name = f"page-{page_index + 1:03d}-image-{image_index:03d}.png"
                    assets.append(
                        ExtractedAsset(
                            element_id,
                            name,
                            "image/png",
                            image_bytes,
                            width_px,
                            height_px,
                        )
                    )
                    elements.append(
                        Element(
                            element_id=element_id,
                            kind="image",
                            page_index=page_index,
                            bbox=bbox,
                            source_object_id=None,
                            provenance=Provenance(
                                backend="pdfium",
                                method="native_image_object_bitmap",
                                source_ref=source_ref,
                                confidence=1.0,
                                unavailable_reason=(
                                    "stable native PDF object ID unavailable"
                                ),
                            ),
                            metadata={
                                "asset_name": name,
                                "media_type": "image/png",
                                "width_px": width_px,
                                "height_px": height_px,
                                "raw_object_index": raw_index,
                            },
                        )
                    )
                elif getattr(obj, "type", None) == 2:
                    elements.append(
                        Element(
                            element_id=f"p{page_index:04d}-vector-{vector_index:05d}",
                            kind="vector",
                            page_index=page_index,
                            bbox=bbox,
                            source_object_id=None,
                            provenance=Provenance(
                                backend="pdfium",
                                method="native_path_object_bounds",
                                source_ref=source_ref,
                                confidence=1.0,
                                unavailable_reason=(
                                    "stable native PDF object ID unavailable"
                                ),
                            ),
                            metadata={"raw_object_index": raw_index},
                        )
                    )
                    vector_index += 1
            ordered = _reading_order(elements, width)
        finally:
            textpage.close()

        if page_degenerate_counts:
            degenerate_pages.append(
                {
                    "page": page_index + 1,
                    "counts": dict(sorted(page_degenerate_counts.items())),
                }
            )

        normalized = [
            Element(
                element_id=item.element_id,
                kind=item.kind,
                page_index=item.page_index,
                bbox=item.bbox,
                provenance=item.provenance,
                text=item.text,
                source_object_id=item.source_object_id,
                metadata={**item.metadata, "normalized_order": order},
            )
            for order, item in enumerate(ordered)
        ]
        return Page(
            page_index=page_index,
            width=width,
            height=height,
            rotation=rotation,
            elements=tuple(normalized),
        )
