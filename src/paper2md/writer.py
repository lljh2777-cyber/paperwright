"""Deterministic PhysicalDocument output writer."""

from __future__ import annotations

import hashlib
import re
import unicodedata
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
    removed = 0
    output = []
    for character in unicodedata.normalize("NFC", text):
        if unicodedata.category(character) == "Cc" and character not in "\t\n":
            removed += 1
            continue
        output.append(character)
    return "".join(output).replace("\u00a0", " "), removed


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


def _page_text_lines(document: PhysicalDocument) -> list[list[Element]]:
    groups: dict[int, list[Element]] = {}
    ungrouped = 1_000_000
    for element in document.pages[0].elements:
        if element.kind != "text" or not element.text:
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
    line_text = [_join_fragments([item.text or "" for item in line]) for line in lines]
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


def _markdown_text_groups(elements: tuple[Element, ...]) -> list[tuple[list[str], str]]:
    result: list[tuple[list[str], str]] = []
    current_key: object = object()
    current: list[Element] = []
    for element in elements:
        if element.kind != "text" or not element.text:
            continue
        key = element.metadata.get("line_group", element.element_id)
        if current and key != current_key:
            result.append(
                (
                    [item.element_id for item in current],
                    _join_fragments([item.text or "" for item in current]),
                )
            )
            current = []
        current_key = key
        current.append(element)
    if current:
        result.append(
            (
                [item.element_id for item in current],
                _join_fragments([item.text or "" for item in current]),
            )
        )
    return result


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

    title, title_element_ids = _title(document)
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
        for element_ids, text in _markdown_text_groups(page.elements):
            if any(element_id in title_element_ids for element_id in element_ids):
                continue
            if text:
                lines.extend(
                    [
                        f"<!-- elements: {','.join(element_ids)}; "
                        f"page: {page.page_index + 1} -->",
                        text,
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
