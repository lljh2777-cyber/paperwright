"""Apply validated hybrid layouts to deterministic Markdown and region assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .backends.base import ExtractedAsset
from .layout_models import (
    FinalLayout,
    LayoutRegion,
    LayoutTask,
)
from .layout_review import validate_layout_review
from .manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    sha256_file,
)
from .models import BBox, Element, Page, PhysicalDocument
from .region_render import RegionRenderRequest
from .writer import _markdown_text_groups, _title


@dataclass(frozen=True)
class PreparedLayoutOutput:
    manifest: dict[str, Any]
    article_path: Path
    physical_document_path: Path


def _trace_digest(element_ids: Sequence[str]) -> str:
    return hashlib.sha256(
        "\n".join(element_ids).encode("utf-8")
    ).hexdigest()


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
            region_elements[regions[0].region_id].update(source_ids)
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
            selected = max(
                regions,
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
) -> PreparedLayoutOutput:
    """Write reviewed layout output without changing the default writer."""

    del assets  # Native assets remain represented in PhysicalDocument provenance.
    if len(tasks) != len(document.pages) or len(layouts) != len(document.pages):
        raise ValueError("布局任务、结果和 PhysicalDocument 页数不一致")
    if visual_scale <= 0:
        raise ValueError("visual_scale 必须为正")

    images_dir = root / "images"
    layout_dir = root / "layout"
    images_dir.mkdir(parents=True)
    layout_dir.mkdir(parents=True)

    physical_path = root / "physical_document.json"
    physical_path.write_text(
        document.canonical_json(),
        encoding="utf-8",
        newline="\n",
    )
    title, title_element_ids = _title(document)
    lines = [f"# {title}", ""]
    image_records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(backend_warnings)
    layout_output_paths: list[Path] = []
    visual_paths: list[Path] = []
    provenance_pages: list[dict[str, Any]] = []

    for page, task, review in zip(document.pages, tasks, layouts):
        materialized = materialize_layout_sources(review, task, page)
        task_path = layout_dir / f"page-{page.page_index + 1:04d}-task.json"
        final_path = layout_dir / f"page-{page.page_index + 1:04d}-final.json"
        task_path.write_text(task.canonical_json(), encoding="utf-8", newline="\n")
        final_path.write_text(
            review.canonical_json(),
            encoding="utf-8",
            newline="\n",
        )
        layout_output_paths.extend((task_path, final_path))
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
                filename = (
                    f"page-{page.page_index + 1:04d}-"
                    f"{region.region_id}-{region.role}.png"
                )
                image_path = images_dir / filename
                image_path.write_bytes(rendered.data)
                visual_paths.append(image_path)
                relative = f"images/{filename}"
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
                }
                image_records.append(image_record)
                lines.extend(
                    [
                        _region_trace_comment(
                            region_id=region.region_id,
                            role=region.role,
                            page_number=page.page_index + 1,
                            element_ids=region.source_element_ids,
                        ),
                        f"![{region.role} from page {page.page_index + 1}]"
                        f"({relative})",
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
                    }
                )
                continue

            text_elements = tuple(
                item
                for item in _ordered_text_elements(page, region)
                if item.element_id not in title_element_ids
            )
            non_text_count = sum(
                item.element_id in set(region.source_element_ids)
                and item.kind != "text"
                for item in page.elements
            )
            if non_text_count:
                warnings.append(
                    {
                        "code": "text_region_contains_non_text_elements",
                        "page": page.page_index + 1,
                        "region_id": region.region_id,
                        "count": non_text_count,
                    }
                )
            paragraphs = _markdown_text_groups(text_elements)
            paragraph_records: list[dict[str, object]] = []
            for paragraph_index, (element_ids, text) in enumerate(paragraphs):
                if not text:
                    continue
                paragraph_records.append(
                    {
                        "paragraph_index": paragraph_index,
                        "source_element_ids": element_ids,
                        "elements_sha256": _trace_digest(element_ids),
                    }
                )
                prefix = (
                    "## "
                    if region.role == "heading" and paragraph_index == 0
                    else ""
                )
                lines.extend(
                    [
                        _region_trace_comment(
                            region_id=region.region_id,
                            role=region.role,
                            page_number=page.page_index + 1,
                            element_ids=element_ids,
                            paragraph_index=paragraph_index,
                        ),
                        prefix + text,
                        "",
                    ]
                )
            page_regions.append(
                {
                    **region.to_dict(),
                    "execution": "extract_native_text",
                    "asset": None,
                    "paragraphs": paragraph_records,
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

    article_path = root / "article.md"
    article_path.write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
        newline="\n",
    )
    provenance = {
        "contract_version": "paper2md-layout-provenance-v0.1",
        "source_sha256": document.source_sha256,
        "candidate_generator_version": tasks[0].candidate_generator_version,
        "feature_schema_version": tasks[0].feature_schema_version,
        "prompt_version": layouts[0].prompt_version,
        "ocr_used": False,
        "pages": provenance_pages,
    }
    provenance_path = layout_dir / "layout-provenance.json"
    provenance_path.write_text(
        json.dumps(
            provenance,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    output_paths = [
        article_path,
        physical_path,
        provenance_path,
        *layout_output_paths,
        *visual_paths,
    ]
    outputs = [
        OutputFile(
            str(path.relative_to(root)).replace("\\", "/"),
            (
                "markdown"
                if path == article_path
                else "physical_document"
                if path == physical_path
                else "layout_provenance"
                if path == provenance_path
                else "visual_region"
                if path in visual_paths
                else "layout_contract"
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
        status="success_with_degradation" if warnings else "success",
        outputs=outputs,
        warnings=warnings,
        elements=element_records,
        images=image_records,
        physical_document={
            "path": "physical_document.json",
            "sha256": hashlib.sha256(
                document.canonical_json().encode("utf-8")
            ).hexdigest(),
        },
        manifest_version=HYBRID_LAYOUT_MANIFEST_VERSION,
        layout_review={
            "mode": "hybrid-reviewed",
            "prompt_version": layouts[0].prompt_version,
            "candidate_generator_version": tasks[0].candidate_generator_version,
            "feature_schema_version": tasks[0].feature_schema_version,
            "provenance_path": "layout/layout-provenance.json",
            "provenance_sha256": sha256_file(provenance_path),
            "ocr_used": False,
            "pages": layout_summary_pages,
        },
    )
    (root / "manifest.json").write_text(
        canonical_manifest_json(manifest),
        encoding="utf-8",
        newline="\n",
    )
    return PreparedLayoutOutput(manifest, article_path, physical_path)
