"""Apply validated hybrid layouts to deterministic Markdown and region assets."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .backends.base import ExtractedAsset
from .evidence import (
    build_run_record,
    build_source_record,
    build_validation_report,
    validate_evidence_level,
    validation_report_markdown,
    write_json,
)
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
from .references import (
    ReferenceParagraph,
    detect_reference_section,
    is_reference_heading,
    removable_back_matter_keys,
    validate_reference_mode,
)
from .region_render import RegionRenderRequest
from .writer import _format_markdown_paragraph, _markdown_text_groups, _title


@dataclass(frozen=True)
class PreparedLayoutOutput:
    manifest: dict[str, Any]
    article_path: Path
    physical_document_path: Path | None


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
    evidence_dir = root / "_paper2md"
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
    visual_index = 0

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
                paragraph_records.append(
                    {
                        "paragraph_index": paragraph_index,
                        "source_element_ids": element_ids,
                        "elements_sha256": _trace_digest(element_ids),
                        "markdown_destination": destination,
                    }
                )
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
                    )
                )
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
                    target_lines.extend(
                        [
                            _region_trace_comment(
                                region_id=region.region_id,
                                role=region.role,
                                page_number=page.page_index + 1,
                                element_ids=element_ids,
                                paragraph_index=paragraph_index,
                            ),
                            prefix + markdown_text,
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
        "contract_version": "paper2md-layout-provenance-v0.1",
        "source_sha256": document.source_sha256,
        "candidate_generator_version": tasks[0].candidate_generator_version,
        "feature_schema_version": tasks[0].feature_schema_version,
        "prompt_version": layouts[0].prompt_version,
        "ocr_used": False,
        "references": references_summary,
        "pages": provenance_pages,
    }
    status = "success_with_degradation" if warnings else "success"
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
                    "_paper2md/source.pdf"
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

    output_paths = [article_path, *visual_paths]
    if references_path is not None:
        output_paths.append(references_path)
    if physical_path is not None:
        output_paths.append(physical_path)
    output_paths.extend(layout_output_paths)
    output_paths.extend(evidence_paths)

    def output_role(path: Path) -> str:
        if path == article_path:
            return "markdown"
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
                "path": "_paper2md/01-physical/physical-document.json",
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
                "_paper2md/04-provenance/layout-provenance.json"
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
    )
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        canonical_manifest_json(manifest),
        encoding="utf-8",
        newline="\n",
    )
    return PreparedLayoutOutput(manifest, article_path, physical_path)
