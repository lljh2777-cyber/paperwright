"""Apply validated hybrid layouts to deterministic Markdown and region assets."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Sequence
import unicodedata

from .article_model import (
    article_model_to_reader,
    canonical_article_model_json,
    render_article_markdown,
    validate_article_model,
)
from .backends.base import ExtractedAsset
from .evidence import (
    build_run_record,
    build_source_record,
    build_validation_report,
    validate_evidence_level,
    validation_report_markdown,
    write_json,
)
from .exceptions import ContractValidationError
from .layout_caption import CaptionBinding, bind_caption_regions
from .layout_continuation import (
    CrossPageParagraphBlock,
    dominant_font_name as _dominant_font_name,
    merge_paragraph_continuations as _merge_cross_page_paragraph_blocks,
)
from .layout_models import (
    FinalLayout,
    LayoutRegion,
    LayoutTask,
)
from .layout_review import layout_task_content_roi, validate_layout_review
from .manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    sha256_file,
)
from .models import BBox, Element, Page, PhysicalDocument
from .quality import (
    analyze_image_links,
    analyze_layout_elements,
    analyze_manifest_inventory,
    analyze_markdown_exclusions,
    analyze_markdown_text,
    analyze_native_object_diagnostics,
    analyze_semantic_layout,
    analyze_title,
    analyze_word_spacing,
)
from .references import (
    ReferenceParagraph,
    detect_reference_section,
    is_reference_heading,
    removable_back_matter_keys,
    validate_reference_mode,
)
from .reader import (
    canonical_reader_json,
    compile_reviewed_article,
    validate_reader_index,
)
from .region_render import RegionRenderRequest
from .text_reconstruction import TEXT_RECONSTRUCTION_VERSION
from .writer import (
    _format_markdown_paragraph,
    _markdown_text_groups,
    _markdown_text_groups_detailed,
    _title,
)


@dataclass(frozen=True)
class PreparedLayoutOutput:
    manifest: dict[str, Any]
    article_path: Path
    physical_document_path: Path | None


@dataclass(frozen=True)
class NativeMatrixEquation:
    equation_id: str
    bbox: BBox
    element_ids: tuple[str, ...]
    paragraph_indexes: tuple[int, ...]


_MATRIX_TOP = frozenset({"⎡", "⎤"})
_MATRIX_MIDDLE = frozenset({"⎢", "⎥"})
_MATRIX_BOTTOM = frozenset({"⎣", "⎦"})
_MATRIX_FRAME = _MATRIX_TOP | _MATRIX_MIDDLE | _MATRIX_BOTTOM


def _union_bbox(elements: Sequence[Element], page: Page, padding: float) -> BBox:
    left = max(0.0, min(item.bbox.x for item in elements) - padding)
    top = max(0.0, min(item.bbox.y for item in elements) - padding)
    right = min(page.width, max(item.bbox.right for item in elements) + padding)
    bottom = min(page.height, max(item.bbox.bottom for item in elements) + padding)
    return BBox(left, top, right - left, bottom - top)


def _normalized_union_bbox(
    elements: Sequence[Element], page: Page
) -> tuple[float, float, float, float] | None:
    """Union bbox of `elements`, normalized to the page (0..1), used as
    column-crossing continuation evidence."""
    if not elements:
        return None
    box = _union_bbox(elements, page, 0.0)
    return (
        box.x / page.width,
        box.y / page.height,
        box.right / page.width,
        box.bottom / page.height,
    )


def _detect_native_matrix_equations(
    page: Page,
    elements: Sequence[Element],
    paragraphs: Sequence[Any],
) -> tuple[NativeMatrixEquation, ...]:
    """Find high-confidence native matrix layouts for lossless local rendering."""

    frame_elements = tuple(
        item
        for item in elements
        if item.text and any(character in _MATRIX_FRAME for character in item.text)
    )
    if len(frame_elements) < 6:
        return ()

    clusters: list[list[Element]] = []
    for item in sorted(frame_elements, key=lambda value: (value.bbox.y, value.bbox.x)):
        if not clusters:
            clusters.append([item])
            continue
        current_bottom = max(value.bbox.bottom for value in clusters[-1])
        if item.bbox.y <= current_bottom + 3.0:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    results: list[NativeMatrixEquation] = []
    for cluster in clusters:
        characters = "".join(item.text or "" for item in cluster)
        if not (
            any(character in _MATRIX_TOP for character in characters)
            and any(character in _MATRIX_BOTTOM for character in characters)
            and len({round(item.bbox.x, 1) for item in cluster}) >= 4
        ):
            continue
        frame_top = min(item.bbox.y for item in cluster)
        frame_bottom = max(item.bbox.bottom for item in cluster)
        frame_left = min(item.bbox.x for item in cluster)
        frame_right = max(item.bbox.right for item in cluster)
        members = tuple(
            item
            for item in elements
            if item.text
            and item.bbox.bottom >= frame_top - 2.0
            and item.bbox.y <= frame_bottom + 2.0
            and item.bbox.right >= frame_left - 4.0
            and item.bbox.x <= frame_right + 4.0
        )
        member_ids = frozenset(item.element_id for item in members)
        paragraph_indexes: list[int] = []
        partial_overlap = False
        for paragraph_index, paragraph in enumerate(paragraphs):
            paragraph_ids = frozenset(paragraph.element_ids)
            if not paragraph_ids & member_ids:
                continue
            if not paragraph_ids <= member_ids:
                partial_overlap = True
                break
            paragraph_indexes.append(paragraph_index)
        contiguous = bool(paragraph_indexes) and paragraph_indexes == list(
            range(paragraph_indexes[0], paragraph_indexes[-1] + 1)
        )
        if partial_overlap or len(paragraph_indexes) < 2 or not contiguous:
            continue
        results.append(
            NativeMatrixEquation(
                equation_id=f"E{len(results) + 1:03d}",
                bbox=_union_bbox(members, page, padding=4.0),
                element_ids=tuple(
                    item.element_id
                    for item in sorted(
                        members,
                        key=lambda value: (
                            value.bbox.y,
                            value.bbox.x,
                            value.element_id,
                        ),
                    )
                ),
                paragraph_indexes=tuple(paragraph_indexes),
            )
        )
    return tuple(results)


def _trace_digest(element_ids: Sequence[str]) -> str:
    return hashlib.sha256(
        "\n".join(element_ids).encode("utf-8")
    ).hexdigest()


def _bbox_intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height


def _text_region_non_text_diagnostics(
    page: Page,
    region: LayoutRegion,
) -> dict[str, object]:
    """Separate harmless text decoration from semantic non-text content."""

    selected = set(region.source_element_ids)
    non_text = tuple(
        item
        for item in page.elements
        if item.element_id in selected and item.kind != "text"
    )
    region_bbox = BBox(
        x=region.bbox.x * page.width,
        y=region.bbox.y * page.height,
        width=region.bbox.width * page.width,
        height=region.bbox.height * page.height,
    )
    thin_limit = max(1.25, min(page.width, page.height) * 0.002)
    classes: Counter[str] = Counter()
    objects: list[dict[str, object]] = []

    for item in non_text:
        classification = "semantic_non_text"
        ignored = False
        if item.kind == "vector":
            short_side = min(item.bbox.width, item.bbox.height)
            long_side = max(item.bbox.width, item.bbox.height)
            aspect_ratio = long_side / max(short_side, 1e-9)
            if short_side <= thin_limit and aspect_ratio >= 8.0:
                classification = "decorative_rule"
                ignored = True
            elif region.role == "heading":
                intersection = _bbox_intersection_area(item.bbox, region_bbox)
                vector_area = item.bbox.width * item.bbox.height
                region_area = region_bbox.width * region_bbox.height
                if (
                    intersection / max(vector_area, 1e-9) >= 0.90
                    and intersection / max(region_area, 1e-9) >= 0.35
                ):
                    classification = "heading_background"
                    ignored = True
        classes[classification] += 1
        objects.append(
            {
                "element_id": item.element_id,
                "kind": item.kind,
                "classification": classification,
                "ignored_as_decoration": ignored,
                "bbox": item.bbox.to_dict(),
            }
        )

    ignored_count = sum(
        bool(item["ignored_as_decoration"]) for item in objects
    )
    return {
        "policy_version": "paperwright-text-region-non-text-v1",
        "total_count": len(objects),
        "ignored_decorative_count": ignored_count,
        "risk_count": len(objects) - ignored_count,
        "by_class": dict(sorted(classes.items())),
        "objects": objects,
    }


def _region_trace_comment(
    *,
    region_id: str,
    role: str,
    page_number: int,
    element_ids: Sequence[str],
    paragraph_index: int | None = None,
) -> str:
    reference = f"page/{page_number}/region/{region_id}"
    if paragraph_index is not None:
        reference += f"/paragraph/{paragraph_index}"
    return (
        f"<!-- layout-region: {region_id}; role: {role}; "
        f"page: {page_number}; element-count: {len(element_ids)}; "
        f"elements-sha256: {_trace_digest(element_ids)}; "
        f"provenance-ref: {reference} -->"
    )


_INTERNAL_MARKDOWN_COMMENTS = (
    "<!-- page:",
    "<!-- layout-region:",
    "<!-- caption-for:",
    "<!-- cross-page-continuation:",
    "<!-- body-continuation:",
    "<!-- paragraph-continuation:",
)


def _clean_user_markdown(lines: Sequence[str]) -> list[str]:
    """Remove internal trace comments while keeping readable spacing."""

    cleaned: list[str] = []
    for line in lines:
        if line.lstrip().startswith(_INTERNAL_MARKDOWN_COMMENTS):
            continue
        if not line and (not cleaned or not cleaned[-1]):
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return cleaned


_CAPTION_LABEL = re.compile(
    r"^(?P<label>(?:fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?)"
    r"\s*(?:[|.:])?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def _format_caption_markdown(text: str) -> str:
    """Emphasize only a leading Figure/Table label, not the whole caption."""

    match = _CAPTION_LABEL.match(text.strip())
    if match is None:
        return text
    label = match.group("label").rstrip(". :|") + "."
    rest = match.group("rest").strip()
    return f"**{label}** {rest}".rstrip()


def _image_alt_text(role: str, page_number: int, caption: str | None) -> str:
    """Return concise, Markdown-safe alternative text for a visual region."""

    if not caption:
        return f"{role.capitalize()} from page {page_number}"
    plain = re.sub(r"[*_`#]", "", " ".join(caption.split()))
    plain = plain.replace("[", "(").replace("]", ")")
    match = _CAPTION_LABEL.match(plain)
    if match is not None:
        label = match.group("label").rstrip(". :|")
        rest = match.group("rest").strip()
        summary = re.split(r"(?<=[.!?])\s+", rest, maxsplit=1)[0]
        first_sentence = f"{label}: {summary}" if summary else label
    else:
        first_sentence = re.split(r"(?<=[.!?])\s+", plain, maxsplit=1)[0]
    if len(first_sentence) <= 180:
        return first_sentence
    shortened = first_sentence[:177].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return shortened + "..."


def _intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height


def _distance_squared(left: BBox, right: BBox) -> float:
    left_x = left.x + left.width / 2
    left_y = left.y + left.height / 2
    right_x = right.x + right.width / 2
    right_y = right.y + right.height / 2
    return (left_x - right_x) ** 2 + (left_y - right_y) ** 2


def materialize_layout_sources(
    layout: FinalLayout,
    task: LayoutTask,
    page: Page,
) -> FinalLayout:
    """Assign real element IDs after AI review, including deterministic splits."""

    validate_layout_review(layout, task)
    if page.page_index != task.page.page_index:
        raise ValueError("布局任务与 PhysicalDocument 页面不一致")
    elements = {item.element_id: item for item in page.elements}
    content_roi = layout_task_content_roi(task)
    content_roi_box = (
        content_roi.to_pdf_bbox(
            page_width=page.width,
            page_height=page.height,
        )
        if content_roi is not None
        else None
    )

    def eligible_for_region(element: Element, region: LayoutRegion) -> bool:
        if region.content_class == "exclude" or content_roi_box is None:
            return True
        element_area = element.bbox.width * element.bbox.height
        if element_area <= 0:
            return False
        return (
            _intersection_area(element.bbox, content_roi_box) / element_area
            >= 0.50
        )

    candidates = {item.candidate_id: item for item in task.candidates}
    assignments: dict[str, list[LayoutRegion]] = {
        candidate_id: [] for candidate_id in candidates
    }
    for region in layout.regions:
        for candidate_id in region.source_candidate_ids:
            assignments[candidate_id].append(region)

    region_elements: dict[str, set[str]] = {
        region.region_id: set() for region in layout.regions
    }
    for candidate_id, regions in assignments.items():
        if not regions:
            continue
        source_ids = candidates[candidate_id].source_element_ids
        if len(regions) == 1:
            region = regions[0]
            region_elements[region.region_id].update(
                element_id
                for element_id in source_ids
                if eligible_for_region(elements[element_id], region)
            )
            continue
        region_boxes = {
            region.region_id: region.bbox.to_pdf_bbox(
                page_width=page.width,
                page_height=page.height,
            )
            for region in regions
        }
        for element_id in source_ids:
            element = elements[element_id]
            eligible_regions = tuple(
                region
                for region in regions
                if eligible_for_region(element, region)
            )
            if not eligible_regions:
                continue
            selected = max(
                eligible_regions,
                key=lambda region: (
                    _intersection_area(
                        element.bbox,
                        region_boxes[region.region_id],
                    ),
                    -_distance_squared(
                        element.bbox,
                        region_boxes[region.region_id],
                    ),
                    region.region_id,
                ),
            )
            region_elements[selected.region_id].add(element_id)

    direct_region_ids = {
        region_id
        for action in layout.actions
        if action.action == "add"
        for region_id in action.result_region_ids
    }
    direct_regions = tuple(
        region
        for region in layout.regions
        if region.region_id in direct_region_ids
        and not region.source_candidate_ids
    )
    if direct_regions:
        direct_boxes = {
            region.region_id: region.bbox.to_pdf_bbox(
                page_width=page.width,
                page_height=page.height,
            )
            for region in direct_regions
        }
        already_assigned = {
            element_id
            for element_ids in region_elements.values()
            for element_id in element_ids
        }
        for element in page.elements:
            if element.element_id in already_assigned:
                continue
            element_area = element.bbox.width * element.bbox.height
            if element_area <= 0:
                continue
            options: list[tuple[float, float, int, str]] = []
            for region in direct_regions:
                if not eligible_for_region(element, region):
                    continue
                intersection = _intersection_area(
                    element.bbox,
                    direct_boxes[region.region_id],
                )
                coverage = intersection / element_area
                if coverage < 0.50:
                    continue
                region_area = region.bbox.width * region.bbox.height
                options.append(
                    (
                        coverage,
                        -region_area,
                        -(region.order or 0),
                        region.region_id,
                    )
                )
            if not options:
                continue
            selected_region_id = max(options)[3]
            region_elements[selected_region_id].add(element.element_id)

    materialized = tuple(
        LayoutRegion(
            region_id=region.region_id,
            bbox=region.bbox,
            content_class=region.content_class,
            role=region.role,
            order=region.order,
            source_candidate_ids=region.source_candidate_ids,
            source_element_ids=tuple(sorted(region_elements[region.region_id])),
            parent_region_id=region.parent_region_id,
            confidence=region.confidence,
        )
        for region in layout.regions
    )
    return FinalLayout(
        source_sha256=layout.source_sha256,
        page=layout.page,
        regions=materialized,
        actions=layout.actions,
        reviewer=layout.reviewer,
        prompt_version=layout.prompt_version,
        warnings=layout.warnings,
    )


def _region_request(
    *,
    page: Page,
    region: LayoutRegion,
    scale: float,
) -> RegionRenderRequest:
    bbox = region.bbox.to_pdf_bbox(
        page_width=page.width,
        page_height=page.height,
    )
    vector_ids = tuple(
        sorted(
            item.element_id
            for item in page.elements
            if item.element_id in region.source_element_ids
            and item.kind == "vector"
        )
    )
    return RegionRenderRequest(
        figure_id=region.region_id,
        page_index=page.page_index,
        bbox=bbox,
        caption_top=page.height + 1.0,
        caption_id=f"{region.region_id}-none",
        caption_element_ids=(),
        caption_text="",
        caption_bbox=BBox(0, max(0.0, page.height - 1.0), 1.0, 1.0),
        caption_reason="layout_review_separate_region",
        caption_confidence=1.0,
        member_element_ids=region.source_element_ids,
        vector_evidence_element_ids=vector_ids[:128],
        vector_evidence_count=len(vector_ids),
        vector_evidence_sha256=hashlib.sha256(
            "\n".join(vector_ids).encode("utf-8")
        ).hexdigest(),
        fallback_reason="hybrid_layout_reviewed_visual_region",
        bbox_rule="reviewed_normalized_bbox_to_pdf_points",
        scale=scale,
        dpi=72.0 * scale,
        max_page_area_ratio=1.01,
        min_variance=0.5,
        caption_guard=0.0,
    )


def _equation_request(
    *,
    page: Page,
    equation: NativeMatrixEquation,
    region_id: str,
    scale: float,
) -> RegionRenderRequest:
    return RegionRenderRequest(
        figure_id=f"{region_id}-{equation.equation_id}",
        page_index=page.page_index,
        bbox=equation.bbox,
        caption_top=page.height + 1.0,
        caption_id=f"{region_id}-{equation.equation_id}-none",
        caption_element_ids=(),
        caption_text="",
        caption_bbox=BBox(0, max(0.0, page.height - 1.0), 1.0, 1.0),
        caption_reason="native_matrix_equation_has_no_caption",
        caption_confidence=1.0,
        member_element_ids=equation.element_ids,
        vector_evidence_element_ids=(),
        vector_evidence_count=0,
        vector_evidence_sha256=hashlib.sha256(b"").hexdigest(),
        fallback_reason="native_matrix_equation_render",
        bbox_rule="native_matrix_frame_union_plus_4pt",
        scale=scale,
        dpi=72.0 * scale,
        max_page_area_ratio=0.25,
        min_variance=0.5,
        caption_guard=0.0,
    )


def _ordered_text_elements(
    page: Page,
    region: LayoutRegion,
) -> tuple[Element, ...]:
    selected = set(region.source_element_ids)
    return tuple(
        sorted(
            (
                item
                for item in page.elements
                if item.element_id in selected
                and item.kind == "text"
                and item.text
            ),
            key=lambda item: (
                item.metadata.get("normalized_order", 1_000_000),
                item.bbox.y,
                item.bbox.x,
                item.element_id,
            ),
        )
    )


def _bind_caption_regions(
    document: PhysicalDocument,
    layouts: Sequence[FinalLayout],
) -> tuple[
    dict[tuple[int, str], CaptionBinding],
    dict[tuple[int, str], CaptionBinding],
    dict[str, Any],
]:
    return bind_caption_regions(
        document,
        layouts,
        caption_text=lambda page, region: " ".join(
            text
            for _, text in _markdown_text_groups(
                _ordered_text_elements(page, region)
            )
            if text
        ),
    )


_STRONG_CAPTION_LINE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?\s*(?:[|.:])",
    re.IGNORECASE,
)


def _validate_materialized_semantics(
    document: PhysicalDocument,
    layouts: Sequence[FinalLayout],
) -> None:
    """Block packaging when an explicit caption remains in prose."""

    for page, layout in zip(document.pages, layouts, strict=True):
        for region in layout.regions:
            if region.content_class != "text" or region.role == "caption":
                continue
            for element in _ordered_text_elements(page, region):
                if element.text and _STRONG_CAPTION_LINE.match(element.text):
                    raise ContractValidationError(
                        "semantic layout failure: explicit Figure/Table "
                        f"caption remains in {region.role} region "
                        f"{region.region_id} on page {page.page_index + 1}"
                    )


def write_layout_outputs(
    *,
    root: Path,
    source: Path,
    document: PhysicalDocument,
    assets: tuple[ExtractedAsset, ...],
    backend_warnings: tuple[dict[str, object], ...],
    tasks: Sequence[LayoutTask],
    layouts: Sequence[FinalLayout],
    region_renderer: Any,
    visual_scale: float = 2.0,
    references_mode: str = "keep",
    evidence_level: str = "standard",
    include_source_pdf: bool = False,
    review_root: Path | None = None,
) -> PreparedLayoutOutput:
    """Write reviewed layout output without changing the default writer."""

    del assets  # Native assets remain represented in PhysicalDocument provenance.
    if len(tasks) != len(document.pages) or len(layouts) != len(document.pages):
        raise ValueError("布局任务、结果和 PhysicalDocument 页数不一致")
    if visual_scale <= 0:
        raise ValueError("visual_scale 必须为正")
    references_mode = validate_reference_mode(references_mode)
    evidence_level = validate_evidence_level(evidence_level)
    if evidence_level in {"standard", "full"} and review_root is None:
        raise ValueError("standard/full evidence requires review_root")

    images_dir = root / "images"
    evidence_dir = root / "_paperwright"
    layout_dir = evidence_dir / "03-layout"
    images_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    physical_path: Path | None = None
    if evidence_level == "full":
        physical_path = evidence_dir / "01-physical/physical-document.json"
        physical_path.parent.mkdir(parents=True)
        physical_path.write_text(
            document.canonical_json(),
            encoding="utf-8",
            newline="\n",
        )
    title, title_element_ids = _title(document)
    lines = [f"# {title}", ""]
    materialized_layouts = tuple(
        materialize_layout_sources(review, task, page)
        for page, task, review in zip(
            document.pages,
            tasks,
            layouts,
            strict=True,
        )
    )
    _validate_materialized_semantics(document, materialized_layouts)
    caption_by_region, caption_by_visual, caption_binding_quality = (
        _bind_caption_regions(document, materialized_layouts)
    )
    caption_text_by_visual: dict[tuple[int, str], str] = {}
    for binding in caption_by_visual.values():
        caption_page = document.pages[binding.caption_page_index]
        caption_layout = materialized_layouts[binding.caption_page_index]
        caption_region = next(
            item
            for item in caption_layout.regions
            if item.region_id == binding.caption_region_id
        )
        caption_text_by_visual[
            (binding.visual_page_index, binding.visual_region_id)
        ] = " ".join(
            text
            for _, text in _markdown_text_groups(
                _ordered_text_elements(caption_page, caption_region)
            )
            if text
        )
    reference_paragraphs: list[ReferenceParagraph] = []
    for page, materialized in zip(
        document.pages,
        materialized_layouts,
        strict=True,
    ):
        for region in sorted(
            (
                item
                for item in materialized.regions
                if item.content_class == "text"
            ),
            key=lambda item: item.order or 0,
        ):
            text_elements = tuple(
                item
                for item in _ordered_text_elements(page, region)
                if item.element_id not in title_element_ids
            )
            for paragraph_index, (_, text) in enumerate(
                _markdown_text_groups(text_elements)
            ):
                if text:
                    reference_paragraphs.append(
                        ReferenceParagraph(
                            page_index=page.page_index,
                            region_id=region.region_id,
                            paragraph_index=paragraph_index,
                            text=text,
                        )
                    )
    reference_section = detect_reference_section(reference_paragraphs)
    reference_keys = (
        {
            item.key
            for item in reference_paragraphs[
                reference_section.start_index : reference_section.end_index
            ]
        }
        if reference_section is not None
        else set()
    )
    back_matter_keys = (
        removable_back_matter_keys(
            reference_paragraphs,
            reference_section.end_index,
            reference_start_index=reference_section.start_index,
        )
        if reference_section is not None
        else frozenset()
    )
    reference_lines: list[str] = (
        ["# References", ""]
        if references_mode == "separate" and reference_section is not None
        else []
    )
    image_records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(backend_warnings)
    layout_output_paths: list[Path] = []
    visual_paths: list[Path] = []
    provenance_pages: list[dict[str, Any]] = []
    quality_paragraphs: list[dict[str, Any]] = []
    reconstruction_events: list[dict[str, Any]] = []
    reconstruction_warnings: list[dict[str, Any]] = []
    cross_page_blocks: list[CrossPageParagraphBlock] = []
    page_marker_indexes: dict[int, int] = {}
    visual_index = 0
    equation_index = 0

    for page, task, review, materialized in zip(
        document.pages,
        tasks,
        layouts,
        materialized_layouts,
        strict=True,
    ):
        if evidence_level in {"standard", "full"}:
            layout_dir.mkdir(parents=True, exist_ok=True)
            final_path = (
                layout_dir
                / f"page-{page.page_index + 1:04d}-final-layout.json"
            )
            final_path.write_text(
                review.canonical_json(),
                encoding="utf-8",
                newline="\n",
            )
            layout_output_paths.append(final_path)
            assert review_root is not None
            source_page_root = (
                review_root / f"page-{page.page_index + 1:04d}"
            )
            overlay_path = (
                layout_dir / f"page-{page.page_index + 1:04d}-overlay.png"
            )
            shutil.copyfile(source_page_root / "overlay.png", overlay_path)
            layout_output_paths.append(overlay_path)
            if evidence_level == "full":
                task_path = (
                    layout_dir
                    / f"page-{page.page_index + 1:04d}-layout-task.json"
                )
                task_path.write_text(
                    task.canonical_json(), encoding="utf-8", newline="\n"
                )
                page_path = (
                    layout_dir / f"page-{page.page_index + 1:04d}-page.png"
                )
                shutil.copyfile(source_page_root / "page.png", page_path)
                layout_output_paths.extend((task_path, page_path))
        page_marker_indexes[page.page_index] = len(lines)
        lines.extend([f"<!-- page: {page.page_index + 1} -->", ""])
        page_regions: list[dict[str, Any]] = []

        for region in sorted(
            (
                item
                for item in materialized.regions
                if item.content_class != "exclude"
            ),
            key=lambda item: item.order or 0,
        ):
            if region.content_class in {"visual", "unknown"}:
                visual_index += 1
                request = _region_request(
                    page=page,
                    region=region,
                    scale=visual_scale,
                )
                rendered = region_renderer.render_region(
                    source,
                    request,
                    expected_source_sha256=document.source_sha256,
                )
                filename = f"figure-{visual_index:04d}.png"
                image_path = images_dir / filename
                image_path.write_bytes(rendered.data)
                visual_paths.append(image_path)
                relative = f"images/{filename}"
                caption_binding = caption_by_visual.get(
                    (page.page_index, region.region_id)
                )
                image_record = {
                    "region_id": region.region_id,
                    "role": region.role,
                    "page": page.page_index + 1,
                    "path": relative,
                    "bbox": rendered.bbox.to_dict(),
                    "width_px": rendered.width_px,
                    "height_px": rendered.height_px,
                    "size_bytes": len(rendered.data),
                    "sha256": rendered.sha256,
                    "renderer_version": rendered.renderer_version,
                    "source_pdf_sha256": rendered.source_sha256,
                    "ocr_used": False,
                    "caption_binding": (
                        caption_binding.to_dict()
                        if caption_binding is not None
                        else None
                    ),
                }
                image_records.append(image_record)
                image_alt = _image_alt_text(
                    region.role,
                    page.page_index + 1,
                    caption_text_by_visual.get(
                        (page.page_index, region.region_id)
                    ),
                )
                lines.extend(
                    [
                        _region_trace_comment(
                            region_id=region.region_id,
                            role=region.role,
                            page_number=page.page_index + 1,
                            element_ids=region.source_element_ids,
                        ),
                        f"![{image_alt}]({relative})",
                        "",
                    ]
                )
                if region.content_class == "unknown":
                    warnings.append(
                        {
                            "code": "layout_region_unknown_rendered",
                            "page": page.page_index + 1,
                            "region_id": region.region_id,
                            "reason": "unknown region preserved as screenshot",
                        }
                    )
                page_regions.append(
                    {
                        **region.to_dict(),
                        "execution": "render_visual",
                        "asset": image_record,
                        "caption_binding": (
                            caption_binding.to_dict()
                            if caption_binding is not None
                            else None
                        ),
                    }
                )
                continue

            text_elements = tuple(
                item
                for item in _ordered_text_elements(page, region)
                if item.element_id not in title_element_ids
            )
            non_text_diagnostics = _text_region_non_text_diagnostics(
                page,
                region,
            )
            if non_text_diagnostics["risk_count"]:
                warnings.append(
                    {
                        "code": "text_region_contains_non_text_elements",
                        "page": page.page_index + 1,
                        "region_id": region.region_id,
                        "count": non_text_diagnostics["risk_count"],
                        "ignored_decorative_count": non_text_diagnostics[
                            "ignored_decorative_count"
                        ],
                        "by_class": non_text_diagnostics["by_class"],
                    }
                )
            paragraphs = _markdown_text_groups_detailed(text_elements)
            equations = _detect_native_matrix_equations(
                page,
                text_elements,
                paragraphs,
            )
            equation_by_paragraph = {
                paragraph_index: equation
                for equation in equations
                for paragraph_index in equation.paragraph_indexes
            }
            equation_records: list[dict[str, object]] = []
            paragraph_records: list[dict[str, object]] = []
            for paragraph_index, paragraph in enumerate(paragraphs):
                element_ids = list(paragraph.element_ids)
                text = paragraph.text
                if not text:
                    continue
                paragraph_key = (
                    page.page_index,
                    region.region_id,
                    paragraph_index,
                )
                is_reference = paragraph_key in reference_keys
                is_back_matter = paragraph_key in back_matter_keys
                destination = (
                    "article"
                    if references_mode == "keep"
                    or not (is_reference or is_back_matter)
                    else references_mode
                    if is_reference
                    else "omit_back_matter"
                )
                equation = equation_by_paragraph.get(paragraph_index)
                paragraph_records.append(
                    {
                        "paragraph_index": paragraph_index,
                        "source_element_ids": element_ids,
                        "elements_sha256": _trace_digest(element_ids),
                        "markdown_destination": destination,
                        "rendered_as": (
                            "equation_image" if equation is not None else "text"
                        ),
                        "first_line_indented": paragraph.first_line_indented,
                        "first_line_indent_state": (
                            paragraph.first_line_indent_state
                        ),
                        "first_line_indent_offset": (
                            paragraph.first_line_indent_offset
                        ),
                        "text_reconstruction": {
                            "version": TEXT_RECONSTRUCTION_VERSION,
                            "events": [
                                item.to_dict() for item in paragraph.events
                            ],
                            "warnings": [
                                item.to_dict() for item in paragraph.warnings
                            ],
                        },
                    }
                )
                for event in paragraph.events:
                    reconstruction_events.append(
                        {
                            "page": page.page_index + 1,
                            "region_id": region.region_id,
                            "paragraph_index": paragraph_index,
                            **event.to_dict(),
                        }
                    )
                for warning in paragraph.warnings:
                    record = {
                        "page": page.page_index + 1,
                        "region_id": region.region_id,
                        "paragraph_index": paragraph_index,
                        **warning.to_dict(),
                    }
                    reconstruction_warnings.append(record)
                    warnings.append(
                        {
                            **record,
                            "detail_code": record["code"],
                            "code": "text_reconstruction_suspicious_unicode",
                        }
                    )
                if equation is not None:
                    target_lines: list[str] | None
                    if destination == "article":
                        target_lines = lines
                    elif destination == "separate":
                        target_lines = reference_lines
                    else:
                        target_lines = None
                    if paragraph_index == equation.paragraph_indexes[0]:
                        equation_record: dict[str, object] = {
                            "equation_id": equation.equation_id,
                            "bbox": equation.bbox.to_dict(),
                            "source_element_ids": list(equation.element_ids),
                            "elements_sha256": _trace_digest(
                                equation.element_ids
                            ),
                            "paragraph_indexes": list(
                                equation.paragraph_indexes
                            ),
                            "asset": None,
                            "markdown_destination": destination,
                            "method": "native_matrix_frame_local_render",
                        }
                        if target_lines is not None:
                            equation_index += 1
                            request = _equation_request(
                                page=page,
                                equation=equation,
                                region_id=region.region_id,
                                scale=visual_scale,
                            )
                            rendered = region_renderer.render_region(
                                source,
                                request,
                                expected_source_sha256=document.source_sha256,
                            )
                            filename = f"equation-{equation_index:04d}.png"
                            image_path = images_dir / filename
                            image_path.write_bytes(rendered.data)
                            visual_paths.append(image_path)
                            relative = f"images/{filename}"
                            image_record = {
                                "region_id": region.region_id,
                                "equation_id": equation.equation_id,
                                "role": "equation",
                                "page": page.page_index + 1,
                                "path": relative,
                                "bbox": rendered.bbox.to_dict(),
                                "width_px": rendered.width_px,
                                "height_px": rendered.height_px,
                                "size_bytes": len(rendered.data),
                                "sha256": rendered.sha256,
                                "renderer_version": rendered.renderer_version,
                                "source_pdf_sha256": rendered.source_sha256,
                                "ocr_used": False,
                            }
                            image_records.append(image_record)
                            equation_record["asset"] = image_record
                            target_lines.extend(
                                [
                                    _region_trace_comment(
                                        region_id=region.region_id,
                                        role="equation",
                                        page_number=page.page_index + 1,
                                        element_ids=equation.element_ids,
                                        paragraph_index=paragraph_index,
                                    ),
                                    f"![equation from page "
                                    f"{page.page_index + 1}]({relative})",
                                    "",
                                ]
                            )
                        equation_records.append(equation_record)
                    continue
                prefix = (
                    "## "
                    if region.role == "heading" and paragraph_index == 0
                    else ""
                )
                markdown_text = (
                    text
                    if prefix
                    else _format_markdown_paragraph(
                        text,
                        element_ids,
                        text_elements,
                        first_line_indented=(
                            paragraph.first_line_indented
                            and region.role == "body"
                        ),
                    )
                )
                if region.role == "caption" and paragraph_index == 0:
                    markdown_text = _format_caption_markdown(markdown_text)
                target_lines: list[str] | None
                if destination == "article":
                    target_lines = lines
                elif destination == "separate":
                    target_lines = reference_lines
                else:
                    target_lines = None
                if target_lines is not None:
                    if destination == "separate" and is_reference_heading(text):
                        continue
                    if destination == "article":
                        quality_paragraphs.append(
                            {
                                "page_index": page.page_index,
                                "region_id": region.region_id,
                                "paragraph_index": paragraph_index,
                                "role": region.role,
                                "text": text,
                                "is_bold": markdown_text.removeprefix(
                                    "&emsp;"
                                ).startswith("**"),
                                "first_line_indented": (
                                    paragraph.first_line_indented
                                    and region.role == "body"
                                ),
                                "first_line_indent_state": (
                                    paragraph.first_line_indent_state
                                ),
                                "reconstruction_events": [
                                    item.to_dict() for item in paragraph.events
                                ],
                            }
                        )
                    trace = _region_trace_comment(
                        region_id=region.region_id,
                        role=region.role,
                        page_number=page.page_index + 1,
                        element_ids=element_ids,
                        paragraph_index=paragraph_index,
                    )
                    caption_binding = caption_by_region.get(
                        (page.page_index, region.region_id)
                    )
                    if caption_binding is not None and paragraph_index == 0:
                        target_lines.append(
                            "<!-- caption-for: page: "
                            f"{caption_binding.visual_page_index + 1}; region: "
                            f"{caption_binding.visual_region_id}; method: "
                            f"{caption_binding.method} -->"
                        )
                    target_lines.extend([trace, prefix + markdown_text, ""])
                    if destination == "article":
                        element_by_id = {
                            item.element_id: item for item in text_elements
                        }
                        paragraph_elements = tuple(
                            element_by_id[element_id]
                            for element_id in element_ids
                            if element_id in element_by_id
                        )
                        last_text = (
                            paragraph_elements[-1].text or ""
                            if paragraph_elements
                            else ""
                        )
                        cross_page_blocks.append(
                            CrossPageParagraphBlock(
                                page_index=page.page_index,
                                region_id=region.region_id,
                                trace_index=len(lines) - 3,
                                text_index=len(lines) - 2,
                                text=prefix + markdown_text,
                                role=region.role,
                                is_bold=markdown_text.removeprefix(
                                    "&emsp;"
                                ).startswith("**"),
                                dominant_font=_dominant_font_name(
                                    paragraph_elements
                                ),
                                ends_with_pdf_soft_break=bool(last_text)
                                and unicodedata.category(last_text[-1]) == "Cc",
                                element_ids=tuple(element_ids),
                                first_line_indented=(
                                    paragraph.first_line_indented
                                    and region.role == "body"
                                ),
                                first_line_indent_state=(
                                    paragraph.first_line_indent_state
                                ),
                                first_line_indent_offset=(
                                    paragraph.first_line_indent_offset
                                ),
                                caption_binding_key=(
                                    (
                                        caption_binding.visual_page_index,
                                        caption_binding.visual_region_id,
                                    )
                                    if caption_binding is not None
                                    else None
                                ),
                                bbox=_normalized_union_bbox(
                                    paragraph_elements, page
                                ),
                            )
                        )
            page_regions.append(
                {
                    **region.to_dict(),
                    "execution": "extract_native_text",
                    "asset": None,
                    "paragraphs": paragraph_records,
                    "caption_binding": (
                        caption_by_region[
                            (page.page_index, region.region_id)
                        ].to_dict()
                        if (page.page_index, region.region_id)
                        in caption_by_region
                        else None
                    ),
                    "equations": equation_records,
                    "non_text_diagnostics": non_text_diagnostics,
                }
            )

        provenance_pages.append(
            {
                "page_index": page.page_index,
                "task_sha256": task.deterministic_sha256(),
                "final_layout_sha256": review.deterministic_sha256(),
                "reviewer": materialized.reviewer,
                "prompt_version": materialized.prompt_version,
                "regions": page_regions,
            }
        )

    continuation_events = _merge_cross_page_paragraph_blocks(
        lines,
        cross_page_blocks,
        page_marker_indexes,
    )
    reconstruction_events.extend(continuation_events)
    reader_compilation = compile_reviewed_article(
        lines,
        document=document,
        title_element_ids=tuple(sorted(title_element_ids)),
        provenance_pages=provenance_pages,
        image_records=image_records,
    )
    article_model_value = reader_compilation.article_model(
        source_sha256=document.source_sha256
    )
    article_model_path = evidence_dir / "article-model.json"
    article_model_path.write_text(
        canonical_article_model_json(article_model_value),
        encoding="utf-8",
        newline="\n",
    )
    validate_article_model(article_model_value, root=root)
    reference_lines = _clean_user_markdown(reference_lines)
    article_path = root / "article.md"
    article_text = render_article_markdown(article_model_value)
    article_path.write_text(article_text, encoding="utf-8", newline="\n")
    reader_value = article_model_to_reader(
        article_model_value,
        root=root,
    )
    reader_path = evidence_dir / "reader.json"
    reader_path.write_text(
        canonical_reader_json(reader_value),
        encoding="utf-8",
        newline="\n",
    )
    validate_reader_index(reader_value, article_text=article_text, root=root)
    markdown_quality = analyze_markdown_text(quality_paragraphs)
    figure_label_quality = markdown_quality.pop("figure_label_leakage")
    element_quality = analyze_layout_elements(
        tasks,
        materialized_layouts,
        document,
    )
    quality_checks: dict[str, dict[str, Any]] = {
        "markdown_text": markdown_quality,
        "word_spacing": analyze_word_spacing(quality_paragraphs),
        "caption_binding": caption_binding_quality,
        "figure_label_leakage": figure_label_quality,
        "semantic_layout": analyze_semantic_layout(
            quality_paragraphs,
            markdown_text=markdown_quality,
            figure_label_leakage=figure_label_quality,
            runtime_warnings=warnings,
        ),
        "title_integrity": analyze_title(title, article_text),
        "image_links": analyze_image_links(article_path, images_dir),
        "layout_element_coverage": element_quality["coverage"],
        "layout_element_uniqueness": element_quality["uniqueness"],
        "markdown_exclusions": analyze_markdown_exclusions(document),
        "native_object_diagnostics": analyze_native_object_diagnostics(
            document
        ),
        "text_reconstruction": {
            "status": "warning" if reconstruction_warnings else "pass",
            "version": TEXT_RECONSTRUCTION_VERSION,
            "repair_count": len(reconstruction_events),
            "warning_count": len(reconstruction_warnings),
            "repairs_by_code": {
                code: sum(
                    item["code"] == code for item in reconstruction_events
                )
                for code in sorted(
                    {item["code"] for item in reconstruction_events}
                )
            },
            "findings": reconstruction_warnings[:100],
        },
        "reader_index": {
            "status": "pass",
            "contract_version": reader_value["contract_version"],
            "block_count": len(reader_value["blocks"]),
            "asset_count": len(reader_value["assets"]),
            "relation_count": len(reader_value["relations"]),
            "article_anchor_count": len(reader_value["blocks"]),
        },
        "article_model": {
            "status": "pass",
            "contract_version": article_model_value["contract_version"],
            "block_count": len(article_model_value["blocks"]),
            "asset_count": len(article_model_value["assets"]),
            "relation_count": len(article_model_value["relations"]),
        },
    }
    quality_warning_codes = {
        "markdown_text": "quality_markdown_text_suspicions",
        "word_spacing": "quality_word_spacing_suspected",
        "caption_binding": "quality_caption_binding_incomplete",
        "figure_label_leakage": "quality_figure_label_leak_suspected",
        "semantic_layout": "quality_semantic_layout_failed",
        "title_integrity": "quality_title_integrity_suspected",
        "image_links": "quality_image_links_invalid",
        "layout_element_coverage": "quality_unassigned_text_objects",
        "layout_element_uniqueness": "quality_duplicate_region_objects",
        "markdown_exclusions": "quality_markdown_exclusions_invalid",
        "native_object_diagnostics": "quality_unplaced_native_objects",
        "text_reconstruction": "quality_text_reconstruction_suspicious_unicode",
        "reader_index": "reader_index_invalid",
        "article_model": "article_model_invalid",
    }
    for name, result in quality_checks.items():
        if result["status"] != "pass":
            warnings.append(
                {
                    "code": quality_warning_codes[name],
                    "check": name,
                    "status": result["status"],
                }
            )
    references_path: Path | None = None
    if (
        references_mode == "separate"
        and reference_section is not None
        and reference_lines
    ):
        references_path = root / "references.md"
        references_path.write_text(
            "\n".join(reference_lines).rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
    references_summary: dict[str, object] = {
        "mode": references_mode,
        "status": (
            "detected" if reference_section is not None else "not_detected"
        ),
        "output_path": (
            str(references_path.relative_to(root)).replace("\\", "/")
            if references_path is not None
            else None
        ),
        "omitted_back_matter_paragraphs": (
            len(back_matter_keys) if references_mode != "keep" else 0
        ),
    }
    if reference_section is not None:
        references_summary.update(
            {
                "start_page_index": reference_section.start.page_index,
                "start_region_id": reference_section.start.region_id,
                "start_paragraph_index": (
                    reference_section.start.paragraph_index
                ),
                "evidence_score": reference_section.evidence_score,
                "evidence_paragraphs": (
                    reference_section.evidence_paragraphs
                ),
                "end_page_index": (
                    reference_section.end.page_index
                    if reference_section.end is not None
                    else None
                ),
                "end_region_id": (
                    reference_section.end.region_id
                    if reference_section.end is not None
                    else None
                ),
                "end_paragraph_index": (
                    reference_section.end.paragraph_index
                    if reference_section.end is not None
                    else None
                ),
                "detection_method": reference_section.detection_method,
            }
        )
    provenance = {
        "contract_version": "paperwright-layout-provenance-v0.5",
        "source_sha256": document.source_sha256,
        "candidate_generator_version": tasks[0].candidate_generator_version,
        "feature_schema_version": tasks[0].feature_schema_version,
        "prompt_version": layouts[0].prompt_version,
        "ocr_used": False,
        "references": references_summary,
        "body_continuation_repairs": [
            item
            for item in continuation_events
            if item["code"]
            in {
                "joined_same_page_body_continuation",
                "joined_cross_page_paragraph",
            }
        ],
        "caption_continuation_repairs": [
            item
            for item in continuation_events
            if item["code"] == "joined_caption_fragment"
        ],
        "cross_page_repairs": [
            item
            for item in continuation_events
            if item["code"] == "joined_cross_page_paragraph"
        ],
        "pages": provenance_pages,
    }
    status = (
        "failed"
        if quality_checks["semantic_layout"]["status"] == "fail"
        else "success_with_degradation"
        if warnings
        else "success"
    )
    evidence_paths: list[Path] = []
    provenance_path: Path | None = None
    if evidence_level in {"standard", "full"}:
        assert review_root is not None
        provenance_path = (
            evidence_dir / "04-provenance/layout-provenance.json"
        )
        write_json(provenance_path, provenance)
        evidence_paths.append(provenance_path)

        roi_dir = evidence_dir / "02-roi"
        roi_dir.mkdir(parents=True, exist_ok=True)
        roi_path = roi_dir / "content-roi.json"
        shutil.copyfile(review_root / "content-roi.json", roi_path)
        evidence_paths.append(roi_path)
        if evidence_level == "full":
            for page in document.pages:
                source_page_root = (
                    review_root / f"page-{page.page_index + 1:04d}"
                )
                roi_preview = (
                    roi_dir
                    / f"page-{page.page_index + 1:04d}-content-roi.png"
                )
                shutil.copyfile(
                    source_page_root / "content-roi.png", roi_preview
                )
                evidence_paths.append(roi_preview)

    included_source_path: Path | None = None
    if include_source_pdf:
        included_source_path = evidence_dir / "source.pdf"
        shutil.copyfile(source, included_source_path)
        evidence_paths.append(included_source_path)

    if evidence_level in {"standard", "full"} or include_source_pdf:
        source_record_path = evidence_dir / "source.json"
        write_json(
            source_record_path,
            build_source_record(
                source=source,
                source_sha256=document.source_sha256,
                page_count=len(document.pages),
                included_path=(
                    "_paperwright/source.pdf"
                    if included_source_path is not None
                    else None
                ),
            ),
        )
        evidence_paths.append(source_record_path)

    if evidence_level in {"standard", "full"}:
        run_path = evidence_dir / "run.json"
        write_json(
            run_path,
            build_run_record(
                source_sha256=document.source_sha256,
                backend=document.backend,
                backend_version=document.backend_version,
                page_count=len(document.pages),
                evidence_level=evidence_level,
                references_mode=references_mode,
                visual_scale=visual_scale,
                status=status,
                task_hashes=[item.deterministic_sha256() for item in tasks],
                final_layout_hashes=[
                    item.deterministic_sha256() for item in layouts
                ],
            ),
        )
        evidence_paths.append(run_path)

        validation = build_validation_report(
            status=status,
            evidence_level=evidence_level,
            page_count=len(document.pages),
            image_count=len(visual_paths),
            warnings=warnings,
            references=references_summary,
            reviewers=[item.reviewer for item in layouts],
            quality_checks=quality_checks,
        )
        validation_dir = evidence_dir / "05-validation"
        validation_json = validation_dir / "validation-report.json"
        validation_md = validation_dir / "validation-report.md"
        write_json(validation_json, validation)
        validation_md.write_text(
            validation_report_markdown(validation),
            encoding="utf-8",
            newline="\n",
        )
        evidence_paths.extend((validation_json, validation_md))

    output_paths = [
        article_path,
        article_model_path,
        reader_path,
        *visual_paths,
    ]
    if references_path is not None:
        output_paths.append(references_path)
    if physical_path is not None:
        output_paths.append(physical_path)
    output_paths.extend(layout_output_paths)
    output_paths.extend(evidence_paths)
    if evidence_level in {"standard", "full"}:
        manifest_inventory = analyze_manifest_inventory(root, output_paths)
        if manifest_inventory["status"] != "pass":
            raise ValueError("manifest 输出清单预检失败")
        quality_checks["manifest_inventory"] = manifest_inventory
        validation["quality_checks"] = quality_checks
        validation["checks"].update(
            {
                "image_links_valid": (
                    quality_checks["image_links"]["status"] == "pass"
                ),
                "layout_element_coverage_complete": (
                    quality_checks["layout_element_coverage"]["status"]
                    == "pass"
                ),
                "layout_element_assignments_unique": (
                    quality_checks["layout_element_uniqueness"]["status"]
                    == "pass"
                ),
                "manifest_inventory_complete": True,
            }
        )
        write_json(validation_json, validation)
        validation_md.write_text(
            validation_report_markdown(validation),
            encoding="utf-8",
            newline="\n",
        )

    def output_role(path: Path) -> str:
        if path == article_path:
            return "markdown"
        if path == reader_path:
            return "reader_index"
        if path == article_model_path:
            return "article_model"
        if path == references_path:
            return "references_markdown"
        if path == physical_path:
            return "physical_document"
        if path == provenance_path:
            return "layout_provenance"
        if path in visual_paths:
            return "visual_region"
        name = path.name
        if name.endswith("-final-layout.json"):
            return "final_layout"
        if name.endswith("-layout-task.json"):
            return "layout_task"
        if name.endswith("-overlay.png"):
            return "layout_overlay"
        if name.endswith("-page.png"):
            return "page_preview"
        if name.endswith("-content-roi.png"):
            return "content_roi_preview"
        if name == "content-roi.json":
            return "content_roi"
        if name.startswith("validation-report"):
            return "validation_report"
        if name == "run.json":
            return "run_metadata"
        if name == "source.json":
            return "source_metadata"
        if name == "source.pdf":
            return "source_pdf"
        return "evidence"

    outputs = [
        OutputFile(
            str(path.relative_to(root)).replace("\\", "/"),
            output_role(path),
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
    ] if evidence_level == "full" else None
    layout_summary_pages = [
        {
            "page_index": page["page_index"],
            "task_sha256": page["task_sha256"],
            "final_layout_sha256": page["final_layout_sha256"],
            "reviewer": page["reviewer"],
            "region_count": len(page["regions"]),
        }
        for page in provenance_pages
    ]
    manifest = build_manifest(
        source_sha256=document.source_sha256,
        backend=document.backend,
        backend_version=document.backend_version,
        contract_version=document.contract_version,
        page_count=len(document.pages),
        status=status,
        outputs=outputs,
        warnings=warnings,
        elements=element_records,
        images=image_records,
        physical_document=(
            {
                "path": "_paperwright/01-physical/physical-document.json",
                "sha256": hashlib.sha256(
                    document.canonical_json().encode("utf-8")
                ).hexdigest(),
            }
            if physical_path is not None
            else None
        ),
        manifest_version=HYBRID_LAYOUT_MANIFEST_VERSION,
        layout_review={
            "mode": "hybrid-reviewed",
            "prompt_version": layouts[0].prompt_version,
            "candidate_generator_version": tasks[0].candidate_generator_version,
            "feature_schema_version": tasks[0].feature_schema_version,
            "provenance_path": (
                "_paperwright/04-provenance/layout-provenance.json"
                if provenance_path is not None
                else None
            ),
            "provenance_sha256": (
                sha256_file(provenance_path)
                if provenance_path is not None
                else None
            ),
            "evidence_level": evidence_level,
            "ocr_used": False,
            "pages": layout_summary_pages,
        },
        reader={
            "contract_version": reader_value["contract_version"],
            "path": "_paperwright/reader.json",
            "sha256": sha256_file(reader_path),
            "article_path": reader_value["article"]["path"],
            "article_sha256": reader_value["article"]["sha256"],
            "anchor_contract": reader_value["article"]["anchor_contract"],
        },
        article_model={
            "contract_version": article_model_value["contract_version"],
            "path": "_paperwright/article-model.json",
            "sha256": sha256_file(article_model_path),
        },
    )
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        canonical_manifest_json(manifest),
        encoding="utf-8",
        newline="\n",
    )
    return PreparedLayoutOutput(manifest, article_path, physical_path)
