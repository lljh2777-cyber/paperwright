"""Deterministic PhysicalDocument output writer."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backends.base import ExtractedAsset
from .manifest import OutputFile, build_manifest, canonical_manifest_json, sha256_file
from .models import Element, PhysicalDocument


@dataclass(frozen=True)
class PreparedOutput:
    manifest: dict[str, Any]
    article_path: Path
    physical_document_path: Path


def _title(document: PhysicalDocument) -> tuple[str, str | None]:
    metadata_title = (
        document.metadata.get("pdf_metadata", {}).get("Title")
        if isinstance(document.metadata.get("pdf_metadata"), dict)
        else None
    )
    if isinstance(metadata_title, str) and metadata_title.strip():
        return metadata_title.strip(), None
    first_page_text = [
        item for item in document.pages[0].elements if item.kind == "text" and item.text
    ]
    if not first_page_text:
        return "Untitled document", None
    candidate = max(
        first_page_text,
        key=lambda item: (
            float(item.metadata.get("font_size") or 0),
            -item.bbox.y,
            -item.bbox.x,
        ),
    )
    return candidate.text or "Untitled document", candidate.element_id


def _table_degradation(page_elements: tuple[Element, ...]) -> bool:
    text = " ".join(item.text or "" for item in page_elements if item.kind == "text")
    vectors = sum(item.kind == "vector" for item in page_elements)
    return "table " in text.casefold() or ("表 " in text and vectors >= 2)


def write_outputs(
    *,
    root: Path,
    document: PhysicalDocument,
    assets: tuple[ExtractedAsset, ...],
    backend_warnings: tuple[dict[str, object], ...],
) -> PreparedOutput:
    images_dir = root / "images"
    images_dir.mkdir(parents=True)

    physical_path = root / "physical_document.json"
    physical_path.write_text(document.canonical_json(), encoding="utf-8")

    title, title_element_id = _title(document)
    lines = [f"# {title}", ""]
    degraded: list[dict[str, Any]] = []
    asset_by_element = {asset.element_id: asset for asset in assets}
    image_records: list[dict[str, Any]] = []

    for page in document.pages:
        lines.extend([f"<!-- page: {page.page_index + 1} -->", ""])
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
        for element in page.elements:
            if (
                element.kind == "text"
                and element.text
                and element.element_id != title_element_id
            ):
                if element.text == title and page.page_index == 0:
                    continue
                lines.extend(
                    [
                        f"<!-- element: {element.element_id}; "
                        f"page: {page.page_index + 1} -->",
                        element.text,
                        "",
                    ]
                )
        # Image placement is page-local and explicit; caption adjacency is not
        # inferred in this MVP.
        for element in page.elements:
            if element.kind != "image" or element.element_id not in asset_by_element:
                continue
            asset = asset_by_element[element.element_id]
            image_path = images_dir / asset.suggested_name
            image_path.write_bytes(asset.data)
            relative = f"images/{asset.suggested_name}"
            digest = sha256_file(image_path)
            lines.extend(
                [
                    f"<!-- element: {element.element_id}; "
                    f"page: {page.page_index + 1}; placement: page-end -->",
                    f"![Extracted image from page {page.page_index + 1}]({relative})",
                    "",
                ]
            )
            image_records.append(
                {
                    "element_id": element.element_id,
                    "path": relative,
                    "page": page.page_index + 1,
                    "bbox": element.bbox.to_dict(),
                    "source_object_id": element.source_object_id,
                    "extraction_method": element.provenance.method,
                    "placement": "page-end",
                    "width_px": asset.width_px,
                    "height_px": asset.height_px,
                    "size_bytes": len(asset.data),
                    "sha256": digest,
                }
            )

    article_path = root / "article.md"
    article_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    output_paths = [article_path, physical_path]
    output_paths.extend(root / item["path"] for item in image_records)
    outputs = [
        OutputFile(
            str(path.relative_to(root)),
            (
                "markdown"
                if path == article_path
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
    if image_records:
        warnings.append(
            {
                "code": "image_placement_page_end",
                "reason": "caption adjacency is not inferred in v2-mvp",
                "count": len(image_records),
            }
        )
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
        degraded=degraded,
        physical_document={
            "path": "physical_document.json",
            "sha256": hashlib.sha256(
                document.canonical_json().encode("utf-8")
            ).hexdigest(),
        },
    )
    (root / "manifest.json").write_text(
        canonical_manifest_json(manifest), encoding="utf-8"
    )
    return PreparedOutput(manifest, article_path, physical_path)
