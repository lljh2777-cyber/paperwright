"""pdfplumber sidecar observations and conservative table proposals."""

from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
from typing import Any

from .exceptions import BackendExecutionError
from .models import BBox, PhysicalDocument
from .source_evidence import text_fingerprint

PDFPLUMBER_PROVIDER_ID = "pdfplumber-geometry"
PDFPLUMBER_ALIGNMENT_VERSION = "paperwright-pdfplumber-geometry-alignment-v0.1"


def _observation_id(page_index: int, kind: str, index: int) -> str:
    return f"{PDFPLUMBER_PROVIDER_ID}:p{page_index:04d}:{kind}:{index:06d}"


def _alignment_id(observation_id: str) -> str:
    digest = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()[:20]
    return f"align-{digest}"


def _paper_bbox(
    item: dict[str, Any],
    page_height: float,
) -> tuple[dict[str, float], dict[str, float], dict[str, float] | None] | None:
    try:
        x0 = float(item["x0"])
        x1 = float(item["x1"])
        if "y0" in item and "y1" in item:
            y0 = float(item["y0"])
            y1 = float(item["y1"])
            top = page_height - y1
            bottom = page_height - y0
        else:
            top = float(item["top"])
            bottom = float(item["bottom"])
            y0 = page_height - bottom
            y1 = page_height - top
    except (KeyError, TypeError, ValueError):
        return None
    raw_provider_bbox = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    if abs(x1 - x0) <= 1e-9:
        x0 = max(0.0, x0 - 0.125)
        x1 += 0.125
    if abs(y1 - y0) <= 1e-9:
        y0 = max(0.0, y0 - 0.125)
        y1 = min(page_height, y1 + 0.125)
        top = page_height - y1
        bottom = page_height - y0
    if x1 <= x0 or y1 <= y0 or bottom <= top:
        return None
    mapping_bbox = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    return (
        raw_provider_bbox,
        {"x": x0, "y": top, "width": x1 - x0, "height": bottom - top},
        mapping_bbox if mapping_bbox != raw_provider_bbox else None,
    )


def _intersection_ratio(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    intersection = width * height
    if intersection <= 0:
        return 0.0
    return intersection / max(left.width * left.height, 1e-9)


def _align(
    *,
    observation_id: str,
    kind: str,
    bbox: BBox,
    text: str | None,
    document: PhysicalDocument,
    page_index: int,
) -> tuple[str | None, dict[str, Any] | None]:
    page = document.pages[page_index]
    target_kind = "text" if kind in {"char", "word"} else "image" if kind == "image" else "vector"
    candidates = [item for item in page.elements if item.kind == target_kind]
    scored: list[tuple[float, float, float, str]] = []
    normalized = " ".join((text or "").split()).casefold()
    for candidate in candidates:
        geometry = _intersection_ratio(bbox, candidate.bbox)
        if geometry <= 0:
            continue
        text_score = 0.0
        if normalized and candidate.text:
            candidate_text = " ".join(candidate.text.split()).casefold()
            text_score = 1.0 if normalized in candidate_text else 0.0
        score = geometry * 0.7 + text_score * 0.3
        scored.append((score, geometry, text_score, candidate.element_id))
    if not scored:
        return None, None
    score, geometry, selected_text_score, element_id = max(
        scored,
        key=lambda item: (item[0], item[1], item[3]),
    )
    threshold = 0.45 if target_kind == "text" else 0.70
    if score < threshold:
        return None, None
    text_score_value = selected_text_score if normalized else None
    return element_id, {
        "alignment_id": _alignment_id(observation_id),
        "provider_id": PDFPLUMBER_PROVIDER_ID,
        "observation_id": observation_id,
        "physical_element_id": element_id,
        "match_basis": "page_kind_geometry_and_normalized_text",
        "algorithm_version": PDFPLUMBER_ALIGNMENT_VERSION,
        "text_score": text_score_value,
        "geometry_score": round(min(1.0, geometry), 6),
    }


def build_pdfplumber_evidence(
    source: Path,
    document: PhysicalDocument,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return provider snapshot, alignments and table-region claims."""

    try:
        import pdfplumber
    except ImportError as exc:
        raise BackendExecutionError(
            "standard source evidence requires pdfplumber==0.11.10"
        ) from exc
    version = importlib.metadata.version("pdfplumber")
    pages: list[dict[str, Any]] = []
    alignments: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    observation_count = 0
    table_failures: list[dict[str, Any]] = []
    try:
        pdf = pdfplumber.open(source)
    except Exception as exc:
        raise BackendExecutionError(f"pdfplumber 无法打开 PDF: {exc}") from exc
    try:
        if len(pdf.pages) != len(document.pages):
            raise BackendExecutionError("pdfplumber page count differs from PDFium")
        for page_index, page in enumerate(pdf.pages):
            canonical_page = document.pages[page_index]
            if (
                abs(float(page.width) - canonical_page.width) > 1e-3
                or abs(float(page.height) - canonical_page.height) > 1e-3
            ):
                raise BackendExecutionError(
                    f"pdfplumber page geometry differs on page {page_index + 1}"
                )
            raw_groups: list[tuple[str, list[dict[str, Any]]]] = [
                ("char", list(page.chars)),
                ("word", list(page.extract_words())),
                ("line", list(page.lines)),
                ("rect", list(page.rects)),
                ("curve", list(page.curves)),
                ("image", list(page.images)),
            ]
            observations: list[dict[str, Any]] = []
            for kind, items in raw_groups:
                for sequence, item in enumerate(items):
                    boxes = _paper_bbox(item, canonical_page.height)
                    if boxes is None:
                        continue
                    provider_bbox, paperwright_bbox, mapping_bbox = boxes
                    bbox = BBox.from_dict(paperwright_bbox)
                    observation_id = _observation_id(page_index, kind, sequence)
                    text = str(item.get("text")) if item.get("text") is not None else None
                    physical_id, alignment = _align(
                        observation_id=observation_id,
                        kind=kind,
                        bbox=bbox,
                        text=text,
                        document=document,
                        page_index=page_index,
                    )
                    observation = {
                        "observation_id": observation_id,
                        "physical_element_id": physical_id,
                        "kind": kind,
                        "provider_coordinate_system": "bottom-left/pdf-point/y-up",
                        "provider_bbox": provider_bbox,
                        "paperwright_bbox": paperwright_bbox,
                        "text": text,
                        "text_fingerprint": text_fingerprint(text),
                        "source_ref": f"page:{page_index}:{kind}-index:{sequence}",
                        "extraction_method": f"pdfplumber_{kind}_geometry",
                        "materialization_status": "not_applicable",
                    }
                    if mapping_bbox is not None:
                        observation["provider_bbox_for_mapping"] = mapping_bbox
                        observation["bbox_adjustment"] = "expand_zero_area_line_to_0.25pt"
                    observations.append(observation)
                    if alignment is not None:
                        alignments.append(alignment)
                    observation_count += 1

            try:
                tables = page.find_tables()
            except Exception as exc:
                table_failures.append(
                    {
                        "page_index": page_index,
                        "code": "table_finder_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                tables = []
            for table_index, table in enumerate(tables):
                x0, top, x1, bottom = (float(value) for value in table.bbox)
                evidence_ids = [
                    item["observation_id"]
                    for item in observations
                    if item["kind"] in {"word", "line", "rect"}
                    and x0 <= item["paperwright_bbox"]["x"] + item["paperwright_bbox"]["width"] / 2 <= x1
                    and top <= item["paperwright_bbox"]["y"] + item["paperwright_bbox"]["height"] / 2 <= bottom
                ]
                if not evidence_ids:
                    continue
                claims.append(
                    {
                        "claim_id": f"pdfplumber-table-p{page_index:04d}-{table_index:04d}",
                        "provider_id": PDFPLUMBER_PROVIDER_ID,
                        "capability": "table_proposal",
                        "claim_type": "table_region",
                        "evidence_observation_ids": evidence_ids,
                        "payload": {
                            "page_index": page_index,
                            "paperwright_bbox": {
                                "x": x0,
                                "y": top,
                                "width": x1 - x0,
                                "height": bottom - top,
                            },
                            "cell_count": len(table.cells),
                            "strategy": "pdfplumber-default-lines-v0.1",
                            "direct_markdown_authority": False,
                        },
                        "status": "proposed",
                    }
                )
            pages.append(
                {
                    "page_index": page_index,
                    "width": canonical_page.width,
                    "height": canonical_page.height,
                    "rotation": canonical_page.rotation,
                    "provider_to_paperwright_affine": [
                        1.0,
                        0.0,
                        0.0,
                        -1.0,
                        0.0,
                        canonical_page.height,
                    ],
                    "observations": observations,
                }
            )
    finally:
        pdf.close()
    snapshot = {
        "contract_version": "paperwright-provider-snapshot-v0.1",
        "provider_id": PDFPLUMBER_PROVIDER_ID,
        "provider_version": version,
        "source_sha256": document.source_sha256,
        "status": "degraded" if table_failures else "complete",
        "capabilities": [
            "character_geometry",
            "image_inventory",
            "table_proposals",
            "vector_geometry",
            "word_geometry",
        ],
        "missing_capabilities": [
            "scholarly_semantic_roles",
            *(["table_proposals_complete"] if table_failures else []),
        ],
        "diagnostics": table_failures,
        "page_count": len(pages),
        "observation_count": observation_count,
        "pages": pages,
    }
    return snapshot, alignments, claims
