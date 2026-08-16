"""Deterministic PhysicalDocument output writer."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .backends.base import ExtractedAsset
from .figures import CaptionCandidate, FigureGroup, analyze_figures, compose_group_png
from .layout_continuation import (
    CrossPageParagraphBlock,
    dominant_font_name,
    merge_paragraph_continuations,
)
from .manifest import (
    AUTO_REGION_MANIFEST_VERSION,
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    sha256_file,
)
from .models import Element, Page, PhysicalDocument
from .content_render import (
    ContentRegionAnalysis,
    analyze_content_regions,
    to_region_render_request,
)
from .completeness import (
    COMPLETENESS_REPORT_PATH,
    build_completeness_report,
    canonical_completeness_json,
    completeness_manifest_record,
    full_page_render_request,
    page_requires_visual_fallback,
)
from .region_render import plan_region_renders
from .text_reconstruction import (
    ReconstructedText,
    clean_text,
    join_line_elements,
    reconstruct_text_groups,
)


@dataclass(frozen=True)
class PreparedOutput:
    manifest: dict[str, Any]
    article_path: Path
    physical_document_path: Path


_GENERIC_TITLE = re.compile(
    r"^(?:[A-Z]{1,8}[-_])?[A-Z0-9_-]+\s+\d+\s*(?:\.\.|/)\s*\d+$",
    re.IGNORECASE,
)
_TITLE_BOILERPLATE = {
    "article",
    "observation",
    "perspective",
    "research article",
    "review",
    "tools and resources",
}


def _clean_text(text: str) -> tuple[str, int]:
    return clean_text(text)


def _join_fragments(fragments: list[str]) -> str:
    value = ""
    for raw in fragments:
        fragment, _ = _clean_text(raw.strip())
        if not fragment:
            continue
        if not value:
            value = fragment
        elif value.endswith(("-", "\u2010", "\u2011", "/")):
            value += fragment
        elif fragment[0] in ",.;:!?)]}%\u00b2\u00b3\u2020*":
            value += fragment
        else:
            value += " " + fragment
    return re.sub(r"[ \t]+", " ", value).strip()


def _join_line_elements(elements: list[Element]) -> str:
    return join_line_elements(elements).text


def _page_text_lines(document: PhysicalDocument) -> list[list[Element]]:
    groups: dict[int, list[Element]] = {}
    ungrouped = 1_000_000
    for element in document.pages[0].elements:
        if (
            element.kind != "text"
            or not element.text
            or element.metadata.get("markdown_excluded_reason")
        ):
            continue
        key = element.metadata.get("line_group")
        if not isinstance(key, int):
            key = ungrouped
            ungrouped += 1
        groups.setdefault(key, []).append(element)
    return [
        sorted(group, key=lambda item: (item.bbox.x, item.bbox.y))
        for _, group in sorted(
            groups.items(),
            key=lambda pair: (
                min(item.bbox.y for item in pair[1]),
                min(item.bbox.x for item in pair[1]),
            ),
        )
    ]


def _normalized_title_match(value: str) -> str:
    value, _ = _clean_text(value)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _title(document: PhysicalDocument) -> tuple[str, set[str]]:
    metadata_title = (
        document.metadata.get("pdf_metadata", {}).get("Title")
        if isinstance(document.metadata.get("pdf_metadata"), dict)
        else None
    )
    lines = _page_text_lines(document)
    line_text = [_join_line_elements(line) for line in lines]
    trusted_metadata = (
        isinstance(metadata_title, str)
        and len(metadata_title.strip()) >= 15
        and not _GENERIC_TITLE.fullmatch(metadata_title.strip())
        and metadata_title.strip().casefold() not in _TITLE_BOILERPLATE
    )
    if trusted_metadata:
        title = _clean_text(metadata_title.strip())[0]
        target = _normalized_title_match(title)
        for start in range(min(len(lines), 30)):
            for count in range(1, min(5, len(lines) - start + 1)):
                candidate = " ".join(line_text[start : start + count])
                if _normalized_title_match(candidate) == target:
                    return title, {
                        item.element_id
                        for line in lines[start : start + count]
                        for item in line
                    }
                window = [
                    item
                    for line in lines[start : start + count]
                    for item in line
                ]
                candidate = _join_line_elements(window)
                if _normalized_title_match(candidate) == target:
                    return title, {item.element_id for item in window}
        return title, set()

    if not lines:
        return "Untitled document", set()
    page_height = document.pages[0].height
    candidates = []
    for index, line in enumerate(lines):
        text = line_text[index]
        top = min(item.bbox.y for item in line)
        bottom = max(item.bbox.y + item.bbox.height for item in line)
        height = bottom - top
        width = max(item.bbox.x + item.bbox.width for item in line) - min(
            item.bbox.x for item in line
        )
        if (
            top <= page_height * 0.38
            and len(text) >= 8
            and text.casefold() not in _TITLE_BOILERPLATE
        ):
            candidates.append((height, width, -top, index))
    if not candidates:
        first = lines[0]
        return line_text[0] or "Untitled document", {
            item.element_id for item in first
        }
    _, _, _, best = max(candidates)
    best_line = lines[best]
    best_height = max(item.bbox.y + item.bbox.height for item in best_line) - min(
        item.bbox.y for item in best_line
    )
    selected = [best]
    next_top = min(item.bbox.y for item in best_line)
    for index in range(best - 1, max(-1, best - 3), -1):
        line = lines[index]
        top = min(item.bbox.y for item in line)
        bottom = max(item.bbox.y + item.bbox.height for item in line)
        height = bottom - top
        if height < best_height * 0.72 or next_top - bottom > best_height * 0.8:
            break
        selected.insert(0, index)
        next_top = top
    previous_bottom = max(
        item.bbox.y + item.bbox.height for item in best_line
    )
    for index in range(best + 1, min(len(lines), best + 4)):
        line = lines[index]
        top = min(item.bbox.y for item in line)
        bottom = max(item.bbox.y + item.bbox.height for item in line)
        height = bottom - top
        if height < best_height * 0.72 or top - previous_bottom > best_height * 0.8:
            break
        selected.append(index)
        previous_bottom = bottom
    title = _join_fragments([line_text[index] for index in selected])
    return title or "Untitled document", {
        item.element_id for index in selected for item in lines[index]
    }


_BOLD_FONT_MARKERS = ("bold", "semibold", "demibold", "black", "heavy")


def _is_bold_text_element(element: Element) -> bool:
    if element.kind != "text" or not element.text:
        return False
    font_name = element.metadata.get("font_name")
    if not isinstance(font_name, str) or not font_name.strip():
        return False
    normalized = font_name.casefold().replace(" ", "")
    return any(marker in normalized for marker in _BOLD_FONT_MARKERS)


def _format_markdown_paragraph(
    text: str,
    element_ids: list[str],
    elements: tuple[Element, ...],
    *,
    first_line_indented: bool = False,
) -> str:
    """Preserve native bold and a confirmed first-line paragraph indent."""
    selected_ids = set(element_ids)
    selected = [
        element
        for element in elements
        if element.element_id in selected_ids
        and element.kind == "text"
        and element.text
    ]
    value = (
        f"**{text}**"
        if selected
        and all(_is_bold_text_element(element) for element in selected)
        else text
    )
    return "&emsp;" + value if first_line_indented else value


def _markdown_text_groups_detailed(
    elements: tuple[Element, ...],
) -> list[ReconstructedText]:
    return reconstruct_text_groups(
        tuple(
            element
            for element in elements
            if not element.metadata.get("markdown_excluded_reason")
        )
    )


def _markdown_text_groups(
    elements: tuple[Element, ...],
) -> list[tuple[list[str], str]]:
    return [
        (list(item.element_ids), item.text)
        for item in _markdown_text_groups_detailed(elements)
    ]


def _normalized_paragraph_bbox(
    elements: Sequence[Element], page: Page
) -> tuple[float, float, float, float] | None:
    """Union bbox of `elements`, normalized to the page (0..1), used as
    column-crossing continuation evidence."""
    if not elements:
        return None
    return (
        min(item.bbox.x for item in elements) / page.width,
        min(item.bbox.y for item in elements) / page.height,
        max(item.bbox.right for item in elements) / page.width,
        max(item.bbox.bottom for item in elements) / page.height,
    )


def _table_degradation(page_elements: tuple[Element, ...]) -> bool:
    text = " ".join(item.text or "" for item in page_elements if item.kind == "text")
    vectors = sum(item.kind == "vector" for item in page_elements)
    return "table " in text.casefold() or ("表 " in text and vectors >= 2)


def _region_render_failure_reason(error: Exception) -> str:
    message = str(error)
    mappings = (
        ("输入 PDF 哈希", "source_pdf_hash_mismatch"),
        ("页码越界", "page_index_out_of_bounds"),
        ("bbox 越界", "bbox_out_of_bounds_or_nonpositive"),
        ("caption guard", "caption_guard_intrusion"),
        ("整页或近整页", "near_full_page_region_rejected"),
        ("像素尺寸为零", "zero_pixel_dimensions"),
        ("像素上限", "pixel_limit_exceeded"),
        ("空白或近恒定", "blank_or_low_variance_region"),
    )
    for fragment, reason in mappings:
        if fragment in message:
            return reason
    return f"render_validation_failed:{type(error).__name__}"


def write_outputs(
    *,
    root: Path,
    document: PhysicalDocument,
    assets: tuple[ExtractedAsset, ...],
    backend_warnings: tuple[dict[str, object], ...],
    source: Path | None = None,
    region_renderer: Any | None = None,
    region_render_page_indices: frozenset[int] = frozenset(),
    region_render_mode: str = "off",
    region_render_max_candidates: int = 12,
) -> PreparedOutput:
    images_dir = root / "images"
    images_dir.mkdir(parents=True)

    physical_path = root / "physical_document.json"
    physical_path.write_text(document.canonical_json(), encoding="utf-8")

    title, title_element_ids = _title(document)
    degraded: list[dict[str, Any]] = []
    asset_by_element = {asset.element_id: asset for asset in assets}
    elements_by_id = {
        element.element_id: element
        for page in document.pages
        for element in page.elements
    }
    image_records: list[dict[str, Any]] = []

    # Native assets are always retained, even when a deterministic Figure
    # composite is added.  This preserves provenance and avoids lossy rewrite.
    for page in document.pages:
        for element in page.elements:
            if element.kind != "image" or element.element_id not in asset_by_element:
                continue
            asset = asset_by_element[element.element_id]
            image_path = images_dir / asset.suggested_name
            image_path.write_bytes(asset.data)
            relative = f"images/{asset.suggested_name}"
            image_records.append(
                {
                    "element_id": element.element_id,
                    "path": relative,
                    "page": page.page_index + 1,
                    "bbox": element.bbox.to_dict(),
                    "source_object_id": element.source_object_id,
                    "extraction_method": element.provenance.method,
                    "placement": "native-retained",
                    "figure_group_id": None,
                    "markdown_referenced": False,
                    "width_px": asset.width_px,
                    "height_px": asset.height_px,
                    "size_bytes": len(asset.data),
                    "sha256": sha256_file(image_path),
                }
            )

    analysis = analyze_figures(document)
    planned_regions = plan_region_renders(
        document,
        analysis,
        mode=region_render_mode,
        max_candidates=region_render_max_candidates,
    )
    region_decisions = (
        tuple(
            item
            for item in planned_regions
            if item.page_index in region_render_page_indices
        )
        if region_render_mode == "explicit"
        else planned_regions
    )
    requested_regions = {
        item.figure_id: item
        for item in region_decisions
        if item.status == "requested"
        and item.figure_id is not None
        and item.request is not None
    }
    region_rejections: list[dict[str, Any]] = [
        {
            "figure_id": item.figure_id,
            "page": item.page_index + 1,
            "reason": item.reason,
            "evidence_element_ids": list(item.evidence_element_ids),
            "evidence_status": "region_render_rejected",
        }
        for item in region_decisions
        if item.status == "rejected"
    ]
    rejected_regions = {
        item.figure_id: item
        for item in region_decisions
        if item.status == "rejected" and item.figure_id is not None
    }

    # Table / display-equation region rendering is opt-in together with
    # figure region rendering, and equally conservative: any render failure
    # leaves the native text in place.
    content_analysis: ContentRegionAnalysis
    if region_render_mode in {"auto", "explicit"}:
        content_analysis = analyze_content_regions(
            document,
            max_candidates=region_render_max_candidates,
        )
    else:
        content_analysis = ContentRegionAnalysis((), ())
    table_records: list[dict[str, Any]] = []
    equation_records: list[dict[str, Any]] = []
    content_path_by_id: dict[str, str] = {}
    content_caption_element_to_id: dict[str, str] = {}
    content_member_element_ids: dict[str, str] = {}
    content_asset_paths: list[Path] = []
    for candidate in (*content_analysis.tables, *content_analysis.equations):
        page = document.pages[candidate.page_index]
        request = to_region_render_request(candidate, page)
        if source is None or region_renderer is None:
            continue
        try:
            rendered = region_renderer.render_region(
                source,
                request,
                expected_source_sha256=document.source_sha256,
            )
        except Exception:
            continue
        relative = f"images/{candidate.content_id}.png"
        content_path = root / relative
        content_path.write_bytes(rendered.data)
        content_asset_paths.append(content_path)
        content_path_by_id[candidate.content_id] = relative
        image_records.append(
            {
                "element_id": f"content:{candidate.content_id}",
                "content_id": candidate.content_id,
                "kind": candidate.kind,
                "path": relative,
                "page": candidate.page_index + 1,
                "bbox": rendered.bbox.to_dict(),
                "width_px": rendered.width_px,
                "height_px": rendered.height_px,
                "size_bytes": len(rendered.data),
                "sha256": rendered.sha256,
                "renderer_version": rendered.renderer_version,
                "source_pdf_sha256": rendered.source_sha256,
            }
        )
        if candidate.caption is not None:
            for element_id in candidate.caption.element_ids:
                content_caption_element_to_id[element_id] = candidate.content_id
        for element_id in candidate.member_element_ids:
            content_member_element_ids[element_id] = candidate.content_id
        record = {
            "content_id": candidate.content_id,
            "kind": candidate.kind,
            "page": candidate.page_index + 1,
            "bbox": rendered.bbox.to_dict(),
            "asset": {
                "path": relative,
                "sha256": rendered.sha256,
                "size_bytes": len(rendered.data),
                "width_px": rendered.width_px,
                "height_px": rendered.height_px,
            },
            "caption": (
                {
                    "caption_id": candidate.caption.caption_id,
                    "element_ids": list(candidate.caption.element_ids),
                    "text": candidate.caption.text,
                    "bbox": candidate.caption.bbox.to_dict(),
                }
                if candidate.caption is not None
                else None
            ),
            "fallback_reason": request.fallback_reason,
            "bbox_rule": candidate.reason,
        }
        if candidate.kind == "table":
            table_records.append(record)
        else:
            equation_records.append(record)

    image_record_by_element = {
        item["element_id"]: item for item in image_records
    }
    figure_records: list[dict[str, Any]] = []
    figure_by_id: dict[str, FigureGroup] = {}
    figure_path_by_id: dict[str, str] = {}
    figure_mode_by_id: dict[str, str] = {}
    figure_asset_paths: list[Path] = []
    for group in analysis.groups:
        figure_by_id[group.figure_id] = group
        if group.extraction_mode == "embedded":
            member_id = group.member_element_ids[0]
            asset_record = image_record_by_element[member_id]
            native_relative = str(asset_record["path"])
            native_width = int(asset_record["width_px"])
            native_height = int(asset_record["height_px"])
            native_size = int(asset_record["size_bytes"])
            native_hash = str(asset_record["sha256"])
        else:
            composite, native_width, native_height = compose_group_png(
                group,
                elements_by_id=elements_by_id,
                assets_by_element=asset_by_element,
            )
            native_relative = f"images/{group.figure_id}.png"
            composite_path = root / native_relative
            composite_path.write_bytes(composite)
            figure_asset_paths.append(composite_path)
            native_size = len(composite)
            native_hash = sha256_file(composite_path)

        relative = native_relative
        asset_width = native_width
        asset_height = native_height
        asset_size = native_size
        asset_hash = native_hash
        extraction_mode = group.extraction_mode
        region_record: dict[str, Any] = {
            "status": "not_requested",
            "reason": "no_conservative_mixed_vector_region_candidate",
            "bbox": None,
            "scale": None,
            "dpi": None,
            "rotation": None,
            "width_px": None,
            "height_px": None,
            "pixel_variance": None,
            "page_area_ratio": None,
            "source_pdf_sha256": None,
            "renderer_version": None,
            "bbox_rule": None,
        }
        rejected_decision = rejected_regions.get(group.figure_id)
        if rejected_decision is not None:
            region_record["status"] = "rejected"
            region_record["reason"] = rejected_decision.reason
        decision = requested_regions.get(group.figure_id)
        effective_caption = group.caption
        effective_caption_status = group.caption_status
        effective_caption_confidence = group.caption_confidence
        effective_caption_reason = group.caption_reason
        if decision is not None and decision.request is not None:
            request = decision.request
            if source is None or region_renderer is None:
                region_record["status"] = "rejected"
                region_record["reason"] = "backend_region_renderer_unavailable"
                region_rejections.append(
                    {
                        "figure_id": group.figure_id,
                        "page": group.page_index + 1,
                        "reason": "backend_region_renderer_unavailable",
                        "evidence_element_ids": list(decision.evidence_element_ids),
                        "evidence_status": "region_render_rejected",
                    }
                )
            else:
                try:
                    rendered = region_renderer.render_region(
                        source,
                        request,
                        expected_source_sha256=document.source_sha256,
                    )
                except Exception as exc:
                    region_record["status"] = "rejected"
                    region_record["reason"] = _region_render_failure_reason(exc)
                    region_rejections.append(
                        {
                            "figure_id": group.figure_id,
                            "page": group.page_index + 1,
                            "reason": region_record["reason"],
                            "evidence_element_ids": list(decision.evidence_element_ids),
                            "evidence_status": "region_render_rejected",
                        }
                    )
                else:
                    relative = f"images/{group.figure_id}-region.png"
                    region_path = root / relative
                    region_path.write_bytes(rendered.data)
                    figure_asset_paths.append(region_path)
                    asset_width = rendered.width_px
                    asset_height = rendered.height_px
                    asset_size = len(rendered.data)
                    asset_hash = rendered.sha256
                    extraction_mode = "region-rendered"
                    region_record = {
                        "status": "rendered",
                        "reason": request.fallback_reason,
                        "bbox": rendered.bbox.to_dict(),
                        "scale": rendered.scale,
                        "dpi": rendered.dpi,
                        "rotation": rendered.page_rotation,
                        "width_px": rendered.width_px,
                        "height_px": rendered.height_px,
                        "pixel_variance": rendered.pixel_variance,
                        "page_area_ratio": rendered.page_area_ratio,
                        "source_pdf_sha256": rendered.source_sha256,
                        "renderer_version": rendered.renderer_version,
                        "bbox_rule": request.bbox_rule,
                    }
                    if effective_caption is None:
                        effective_caption = CaptionCandidate(
                            caption_id=request.caption_id,
                            page_index=group.page_index,
                            label="",
                            element_ids=request.caption_element_ids,
                            text=request.caption_text,
                            bbox=request.caption_bbox,
                        )
                        effective_caption_status = "matched"
                        effective_caption_confidence = request.caption_confidence
                        effective_caption_reason = request.caption_reason
        figure_path_by_id[group.figure_id] = relative
        figure_mode_by_id[group.figure_id] = extraction_mode
        for member_id in group.member_element_ids:
            record = image_record_by_element[member_id]
            record["figure_group_id"] = group.figure_id
            record["markdown_referenced"] = (
                extraction_mode == "embedded"
            )
        caption = effective_caption
        degraded_reasons = [
            item
            for item in group.degraded_reasons
            if not (
                extraction_mode == "region-rendered"
                and item == "vector_evidence_not_rendered"
            )
        ]
        if rejected_decision is not None:
            degraded_reasons.append(
                f"region_render_rejected:{rejected_decision.reason}"
            )
        figure_records.append(
            {
                "figure_id": group.figure_id,
                "page": group.page_index + 1,
                "bbox": (
                    region_record["bbox"]
                    if extraction_mode == "region-rendered"
                    else group.bbox.to_dict()
                ),
                "member_element_ids": list(group.member_element_ids),
                "source_object_ids": [
                    elements_by_id[item].source_object_id
                    for item in group.member_element_ids
                ],
                "extraction_mode": extraction_mode,
                "asset": {
                    "path": relative,
                    "sha256": asset_hash,
                    "size_bytes": asset_size,
                    "width_px": asset_width,
                    "height_px": asset_height,
                },
                "native_asset": {
                    "mode": group.extraction_mode,
                    "path": native_relative,
                    "sha256": native_hash,
                    "size_bytes": native_size,
                    "width_px": native_width,
                    "height_px": native_height,
                    "retained_for_provenance": True,
                },
                "region_render": region_record,
                "caption": {
                    "status": effective_caption_status,
                    "confidence": effective_caption_confidence,
                    "reason": effective_caption_reason,
                    "caption_id": caption.caption_id if caption else None,
                    "element_ids": list(caption.element_ids) if caption else [],
                    "text": caption.text if caption else None,
                    "text_sha256": (
                        hashlib.sha256(caption.text.encode("utf-8")).hexdigest()
                        if caption
                        else None
                    ),
                    "bbox": caption.bbox.to_dict() if caption else None,
                    "matching_rule": (
                        "same_page_explicit_marker_and_geometry"
                        if group.caption_status == "matched"
                        else "unique_same_page_explicit_caption_with_vector_bridge"
                        if extraction_mode == "region-rendered"
                        and effective_caption_status == "matched"
                        else None
                    ),
                },
                "evidence_status": (
                    "complete_region_rendered_mixed_figure"
                    if extraction_mode == "region-rendered"
                    else group.evidence_status
                ),
                "degraded_reasons": degraded_reasons,
                "vector_evidence": {
                    "element_ids_sample": (
                        list(decision.request.vector_evidence_element_ids)
                        if extraction_mode == "region-rendered"
                        and decision is not None
                        and decision.request is not None
                        else list(group.vector_evidence_element_ids)
                    ),
                    "count": (
                        decision.request.vector_evidence_count
                        if extraction_mode == "region-rendered"
                        and decision is not None
                        and decision.request is not None
                        else group.vector_evidence_count
                    ),
                    "element_ids_sha256": (
                        decision.request.vector_evidence_sha256
                        if extraction_mode == "region-rendered"
                        and decision is not None
                        and decision.request is not None
                        else group.vector_evidence_sha256
                    ),
                    "rendered_into_asset": extraction_mode == "region-rendered",
                    "reason": (
                        "PDFium clipped page render includes same-page vector evidence"
                        if extraction_mode == "region-rendered"
                        else
                        "native path bounds are provenance evidence only; "
                        "the bitmap composite does not claim vector rendering"
                    ),
                },
                "markdown_placement": (
                    "immediately-before-caption"
                    if effective_caption_status == "matched"
                    else "page-end-degraded"
                ),
            }
        )

    projected_text_counts: Counter[int] = Counter()
    if any(
        item.kind == "text"
        and bool((item.text or "").strip())
        and not item.metadata.get("markdown_excluded_reason")
        for item in document.pages[0].elements
    ):
        projected_text_counts[0] = 1
    projected_visual_counts: Counter[int] = Counter(
        item["page"] - 1
        for item in (*figure_records, *table_records, *equation_records)
    )
    fallback_pages: set[int] = set()
    unresolved_pages: dict[int, str] = {}
    bound_caption_ids = {
        record["caption"]["caption_id"]
        for record in figure_records
        if record["caption"]["status"] == "matched"
        and record["caption"]["caption_id"] is not None
    }
    bound_caption_ids.update(
        record["caption"]["caption_id"]
        for record in table_records
        if record["caption"] is not None
    )
    orphan_caption_counts: Counter[int] = Counter(
        caption.page_index
        for caption in analysis.caption_candidates
        if caption.caption_id not in bound_caption_ids
    )

    lines = [f"# {title}", ""]
    emitted_figures: set[str] = set()
    matched_by_caption_element: dict[str, str] = {}
    for record in figure_records:
        if record["caption"]["status"] == "matched":
            for element_id in record["caption"]["element_ids"]:
                matched_by_caption_element[element_id] = record["figure_id"]

    def emit_figure(group: FigureGroup, placement: str) -> None:
        if group.figure_id in emitted_figures:
            return
        relative = figure_path_by_id[group.figure_id]
        lines.extend(
            [
                f"<!-- figure: {group.figure_id}; page: {group.page_index + 1}; "
                f"mode: {figure_mode_by_id[group.figure_id]}; placement: {placement}; "
                f"members: {','.join(group.member_element_ids)} -->",
                f"![Figure from page {group.page_index + 1}]({relative})",
                "",
            ]
        )
        emitted_figures.add(group.figure_id)

    emitted_content: set[str] = set()
    content_kind_by_id = {
        item["content_id"]: item["kind"]
        for item in (*table_records, *equation_records)
    }
    content_page_by_id = {
        item["content_id"]: item["page"]
        for item in (*table_records, *equation_records)
    }

    def emit_content(content_id: str, placement: str) -> None:
        if content_id in emitted_content or content_id not in content_path_by_id:
            return
        kind = content_kind_by_id[content_id]
        label = "Table" if kind == "table" else "Equation"
        relative = content_path_by_id[content_id]
        page_number = content_page_by_id[content_id]
        lines.extend(
            [
                f"<!-- content: {content_id}; kind: {kind}; "
                f"page: {page_number}; placement: {placement} -->",
                f"![{label} from page {page_number}]({relative})",
                "",
            ]
        )
        emitted_content.add(content_id)

    page_marker_indexes: dict[int, int] = {}
    cross_page_blocks: list[CrossPageParagraphBlock] = []
    for page in document.pages:
        page_marker_indexes[page.page_index] = len(lines)
        lines.extend([f"<!-- page: {page.page_index + 1} -->", ""])
        if page_requires_visual_fallback(
            page,
            projected_text_count=projected_text_counts[page.page_index],
            projected_visual_count=projected_visual_counts[page.page_index],
        ):
            if source is None or region_renderer is None:
                unresolved_pages[page.page_index] = (
                    "native_text_missing_renderer_unavailable"
                )
                lines.extend(
                    [
                        "> [!WARNING] 本页没有可用文字层，且整页视觉兜底不可用。",
                        "",
                    ]
                )
            else:
                try:
                    rendered = region_renderer.render_region(
                        source,
                        full_page_render_request(page),
                        expected_source_sha256=document.source_sha256,
                    )
                except Exception as exc:
                    unresolved_pages[page.page_index] = (
                        "native_text_missing_full_page_render_failed:"
                        f"{_region_render_failure_reason(exc)}"
                    )
                    lines.extend(
                        [
                            "> [!WARNING] 本页没有可用文字层，整页视觉兜底失败；需要人工复核。",
                            "",
                        ]
                    )
                else:
                    filename = f"page-{page.page_index + 1:03d}-fallback.png"
                    relative = f"images/{filename}"
                    fallback_path = root / relative
                    fallback_path.write_bytes(rendered.data)
                    fallback_pages.add(page.page_index)
                    projected_visual_counts[page.page_index] += 1
                    image_records.append(
                        {
                            "element_id": (
                                f"fallback:page-{page.page_index + 1:04d}"
                            ),
                            "kind": "page_fallback",
                            "path": relative,
                            "page": page.page_index + 1,
                            "bbox": rendered.bbox.to_dict(),
                            "placement": "full-page-completeness-fallback",
                            "markdown_referenced": True,
                            "width_px": rendered.width_px,
                            "height_px": rendered.height_px,
                            "size_bytes": len(rendered.data),
                            "sha256": rendered.sha256,
                            "renderer_version": rendered.renderer_version,
                            "source_pdf_sha256": rendered.source_sha256,
                            "fallback_reason": (
                                "native_text_missing_full_page_fallback"
                            ),
                        }
                    )
                    lines.extend(
                        [
                            "<!-- completeness-fallback: full-page; "
                            f"page: {page.page_index + 1}; ocr: false -->",
                            f"![Full page fallback from page "
                            f"{page.page_index + 1}]({relative})",
                            "",
                        ]
                    )
        page_degraded = _table_degradation(page.elements)
        if page_degraded:
            warning = {
                "code": "table_structure_degraded",
                "page": page.page_index + 1,
                "reason": "deterministic MVP does not infer semantic rows or columns",
            }
            degraded.append(warning)
            lines.extend(
                [
                    "> [!WARNING] 表格结构未重建；以下内容按原始文本保留（degraded）。",
                    "",
                ]
            )
        for paragraph in _markdown_text_groups_detailed(page.elements):
            element_ids = list(paragraph.element_ids)
            text = paragraph.text
            if any(element_id in title_element_ids for element_id in element_ids):
                continue
            matched_ids = {
                matched_by_caption_element[element_id]
                for element_id in element_ids
                if element_id in matched_by_caption_element
            }
            for figure_id in sorted(matched_ids):
                emit_figure(figure_by_id[figure_id], "caption-adjacent")
            content_ids = {
                content_id
                for element_id in element_ids
                for content_id in (
                    [content_caption_element_to_id[element_id]]
                    if element_id in content_caption_element_to_id
                    else []
                )
                + (
                    [content_member_element_ids[element_id]]
                    if element_id in content_member_element_ids
                    else []
                )
            }
            for content_id in sorted(content_ids):
                emit_content(content_id, "native-position")
            is_caption_content = any(
                element_id in content_caption_element_to_id
                for element_id in element_ids
            )
            if content_ids and not is_caption_content and all(
                element_id in content_member_element_ids
                for element_id in element_ids
            ):
                continue
            if text:
                projected_text_counts[page.page_index] += 1
                markdown_text = _format_markdown_paragraph(
                    text,
                    element_ids,
                    page.elements,
                    first_line_indented=paragraph.first_line_indented,
                )
                lines.extend(
                    [
                        f"<!-- elements: {','.join(element_ids)}; "
                        f"page: {page.page_index + 1} -->",
                        markdown_text,
                        "",
                    ]
                )
                paragraph_elements = tuple(
                    elements_by_id[element_id]
                    for element_id in element_ids
                    if element_id in elements_by_id
                )
                last_text = (
                    paragraph_elements[-1].text or ""
                    if paragraph_elements
                    else ""
                )
                is_caption = bool(matched_ids)
                cross_page_blocks.append(
                    CrossPageParagraphBlock(
                        page_index=page.page_index,
                        region_id=f"direct-p{page.page_index}-"
                        f"{len(lines) - 3}",
                        trace_index=len(lines) - 3,
                        text_index=len(lines) - 2,
                        text=markdown_text,
                        role="caption" if is_caption else "body",
                        is_bold=markdown_text.removeprefix(
                            "&emsp;"
                        ).startswith("**"),
                        dominant_font=dominant_font_name(paragraph_elements),
                        ends_with_pdf_soft_break=bool(last_text)
                        and unicodedata.category(last_text[-1]) == "Cc",
                        element_ids=tuple(element_ids),
                        first_line_indented=paragraph.first_line_indented,
                        first_line_indent_state=(
                            paragraph.first_line_indent_state
                        ),
                        first_line_indent_offset=(
                            paragraph.first_line_indent_offset
                        ),
                        caption_binding_key=(
                            (
                                page.page_index,
                                f"figure-{sorted(matched_ids)[0]}",
                            )
                            if is_caption
                            else None
                        ),
                        bbox=_normalized_paragraph_bbox(
                            paragraph_elements, page
                        ),
                    )
                )
        for group in analysis.groups:
            if group.page_index == page.page_index and group.figure_id not in emitted_figures:
                emit_figure(group, "page-end-degraded")
        for content_id, page_number in sorted(
            content_page_by_id.items(),
            key=lambda item: (item[1], item[0]),
        ):
            if page_number == page.page_index + 1:
                emit_content(content_id, "page-end-degraded")

    merge_paragraph_continuations(
        lines,
        cross_page_blocks,
        page_marker_indexes,
    )
    article_path = root / "article.md"
    article_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    completeness_report = build_completeness_report(
        document,
        projected_text_counts=projected_text_counts,
        projected_visual_counts=projected_visual_counts,
        fallback_pages=tuple(sorted(fallback_pages)),
        unresolved_pages=unresolved_pages,
        orphan_caption_counts=orphan_caption_counts,
    )
    completeness_path = root / COMPLETENESS_REPORT_PATH
    completeness_path.parent.mkdir(parents=True, exist_ok=True)
    completeness_path.write_text(
        canonical_completeness_json(completeness_report),
        encoding="utf-8",
        newline="\n",
    )

    output_paths = [article_path, physical_path, completeness_path]
    output_paths.extend(root / item["path"] for item in image_records)
    output_paths.extend(figure_asset_paths)
    output_paths.extend(content_asset_paths)
    output_paths = list(dict.fromkeys(output_paths))
    outputs = [
        OutputFile(
            str(path.relative_to(root)),
            (
                "markdown"
                if path == article_path
                else "completeness_report"
                if path == completeness_path
                else "physical_document"
                if path == physical_path
                else "image"
            ),
            path.stat().st_size,
            sha256_file(path),
        )
        for path in output_paths
    ]
    element_records = [
        {
            "element_id": element.element_id,
            "kind": element.kind,
            "page": page.page_index + 1,
            "bbox": element.bbox.to_dict(),
            "source_object_id": element.source_object_id,
            "provenance": element.provenance.to_dict(),
        }
        for page in document.pages
        for element in page.elements
    ]
    warnings = list(backend_warnings)
    warnings.extend(degraded)
    for page_index in sorted(fallback_pages):
        warnings.append(
            {
                "code": "full_page_completeness_fallback_rendered",
                "page": page_index + 1,
                "status": "warning",
                "reason": "native text was unavailable; the source page was preserved as a deterministic image without OCR",
            }
        )
    if completeness_report["status"] != "pass":
        warnings.append(
            {
                "code": "quality_page_completeness_incomplete",
                "check": "page_completeness",
                "status": completeness_report["status"],
                "count": len(completeness_report["findings"]),
            }
        )
    control_count = sum(
        _clean_text(element.text or "")[1]
        for page in document.pages
        for element in page.elements
        if element.kind == "text"
    )
    if control_count:
        warnings.append(
            {
                "code": "text_control_characters_sanitized",
                "count": control_count,
                "reason": "C0 controls are retained in PhysicalDocument provenance but omitted from Markdown",
            }
        )
    unmatched_count = sum(
        group.caption_status != "matched" for group in analysis.groups
    )
    if unmatched_count:
        warnings.append(
            {
                "code": "figure_caption_unmatched_or_ambiguous",
                "reason": "only same-page high-confidence deterministic matches are adjacent",
                "count": unmatched_count,
            }
        )
    if analysis.rejections:
        warnings.append(
            {
                "code": "figure_candidates_filtered",
                "count": len(analysis.rejections),
                "reason": "small unpaired native images remain in provenance but are not promoted to Figures",
            }
        )
    grouped_vector_degraded = sum(
        item["extraction_mode"] == "grouped"
        and item["vector_evidence"]["count"] > 0
        and not item["vector_evidence"]["rendered_into_asset"]
        for item in figure_records
    )
    if grouped_vector_degraded:
        warnings.append(
            {
                "code": "grouped_figure_vector_evidence_not_rendered",
                "count": grouped_vector_degraded,
                "reason": "group composite contains native bitmap members only; vector bounds remain provenance evidence",
            }
        )
    manifest = build_manifest(
        source_sha256=document.source_sha256,
        backend=document.backend,
        backend_version=document.backend_version,
        contract_version=document.contract_version,
        page_count=len(document.pages),
        status=(
            "failed"
            if completeness_report["status"] == "fail"
            else "success_with_degradation"
            if warnings
            else "success"
        ),
        outputs=outputs,
        warnings=warnings,
        elements=element_records,
        images=image_records,
        figures=figure_records,
        figure_rejections=list(analysis.rejections) + region_rejections,
        degraded=degraded,
        tables=table_records or None,
        equations=equation_records or None,
        completeness=completeness_manifest_record(
            completeness_report,
            report_sha256=sha256_file(completeness_path),
        ),
        physical_document={
            "path": "physical_document.json",
            "sha256": hashlib.sha256(
                document.canonical_json().encode("utf-8")
            ).hexdigest(),
        },
        manifest_version=(
            AUTO_REGION_MANIFEST_VERSION
            if region_render_mode in {"explicit", "auto"}
            else "paperwright-manifest-v0.4"
        ),
        region_render_policy=(
            {
                "mode": region_render_mode,
                "page_indices": sorted(region_render_page_indices),
                "max_candidates_per_document": region_render_max_candidates,
            }
            if region_render_mode in {"explicit", "auto"}
            else None
        ),
    )
    (root / "manifest.json").write_text(
        canonical_manifest_json(manifest), encoding="utf-8"
    )
    return PreparedOutput(manifest, article_path, physical_path)
