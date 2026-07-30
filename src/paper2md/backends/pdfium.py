"""Thin pypdfium2 adapter for born-digital PDFs.

This module delegates parsing, font decoding, image decoding, and rendering to
PDFium.  It only normalizes native page objects into PhysicalDocument v0.2.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
from pathlib import Path
from typing import Any

from ..config import Paper2MDConfig
from ..exceptions import BackendExecutionError, BackendUnavailableError
from ..models import BBox, Element, Page, PhysicalDocument, Provenance
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
        try:
            document = pdfium.PdfDocument(source)
        except Exception as exc:
            raise BackendExecutionError(f"PDFium 无法打开 PDF: {exc}") from exc

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
                "text_order": "deterministic_basic_columns_v1",
            },
        )
        return BackendResult(physical, tuple(assets), tuple(warnings))

    def _extract_page(
        self,
        page: Any,
        page_index: int,
        assets: list[ExtractedAsset],
        warnings: list[dict[str, object]],
    ) -> Page:
        pdfium = self._pdfium
        width, height = float(page.get_width()), float(page.get_height())
        rotation = int(page.get_rotation())
        textpage = page.get_textpage()
        elements: list[Element] = []
        image_index = 0
        vector_index = 0
        try:
            for raw_index, obj in enumerate(page.get_objects()):
                bbox = _clamped_bbox(obj.get_bounds(), width, height)
                if bbox is None:
                    warnings.append(
                        {
                            "code": "degenerate_object_bbox",
                            "page": page_index + 1,
                            "raw_object_index": raw_index,
                        }
                    )
                    continue
                source_ref = f"page:{page_index}:native-object-index:{raw_index}"
                if isinstance(obj, pdfium.PdfTextObj):
                    text = textpage.get_text_bounded(*obj.get_bounds()).strip()
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
                                method="native_text_object_bounded_text",
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
        finally:
            textpage.close()

        ordered = _reading_order(elements, width)
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
