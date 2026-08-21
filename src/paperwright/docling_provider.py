"""Optional page-scoped Docling evidence provider.

Docling is intentionally not a core dependency.  When explicitly enabled,
PaperWright converts only pages selected by deterministic conflict routing and
keeps a provenance-bearing DoclingDocument subset.  Markdown exports are never
requested or trusted.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .models import BBox, PhysicalDocument
from .source_evidence import text_fingerprint
from .specialist_routing import DOCLING_PROVIDER_ID

DOCLING_ALIGNMENT_VERSION = "paperwright-docling-native-alignment-v0.1"
_ITEM_COLLECTIONS = (
    "texts",
    "tables",
    "pictures",
    "key_value_items",
    "form_items",
    "field_regions",
    "field_items",
)
_ROLE_BY_LABEL = {
    "title": "title",
    "section_header": "section_heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "list_item": "list_item",
    "caption": "caption",
    "table": "table_region",
    "picture": "figure_region",
    "formula": "display_equation",
    "code": "code",
    "page_header": "page_header",
    "page_footer": "page_footer",
}


def unavailable_docling_snapshot(
    source_sha256: str,
    *,
    reason: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract_version": "paperwright-provider-snapshot-v0.1",
        "provider_id": DOCLING_PROVIDER_ID,
        "provider_version": "unavailable",
        "source_sha256": source_sha256,
        "status": "unavailable",
        "capabilities": [],
        "missing_capabilities": [
            "layout_semantic_roles",
            "reading_order",
            "table_structure",
            "visual_object_detection",
        ],
        "diagnostics": [
            {
                "code": reason,
                "request_count": len(requests),
                "requested_page_indices": sorted(
                    {
                        page_index
                        for item in requests
                        for page_index in item["scope"]["page_indices"]
                    }
                ),
            }
        ],
        "page_count": 0,
        "observation_count": 0,
        "pages": [],
        "docling_document_subset": {
            "selected_only": True,
            "items": [],
        },
    }


def _bbox_from_provenance(
    provenance: dict[str, Any],
    page_width: float,
    page_height: float,
) -> tuple[BBox, dict[str, Any]] | None:
    raw = provenance.get("bbox")
    if not isinstance(raw, dict):
        return None
    try:
        left = min(float(raw["l"]), float(raw["r"]))
        right = max(float(raw["l"]), float(raw["r"]))
        first_y = float(raw["t"])
        second_y = float(raw["b"])
    except (KeyError, TypeError, ValueError):
        return None
    origin = str(raw.get("coord_origin", "TOPLEFT")).upper()
    if origin == "BOTTOMLEFT":
        top = page_height - max(first_y, second_y)
        bottom = page_height - min(first_y, second_y)
    else:
        top = min(first_y, second_y)
        bottom = max(first_y, second_y)
        origin = "TOPLEFT"
    tolerance = 1e-3
    if (
        right - left <= 0
        or bottom - top <= 0
        or left < -tolerance
        or top < -tolerance
        or right > page_width + tolerance
        or bottom > page_height + tolerance
    ):
        return None
    left = max(0.0, left)
    top = max(0.0, top)
    right = min(page_width, right)
    bottom = min(page_height, bottom)
    return BBox(left, top, right - left, bottom - top), {
        "l": float(raw["l"]),
        "t": first_y,
        "r": float(raw["r"]),
        "b": second_y,
        "coord_origin": origin,
    }


def _intersection_over_left(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height / max(left.width * left.height, 1e-9)


def _scopes_by_page(requests: Iterable[dict[str, Any]]) -> dict[int, list[BBox | None]]:
    result: dict[int, list[BBox | None]] = {}
    for item in requests:
        scope = item.get("scope")
        if not isinstance(scope, dict):
            continue
        raw_bbox = scope.get("paperwright_bbox")
        try:
            bbox = BBox.from_dict(raw_bbox) if isinstance(raw_bbox, dict) else None
        except (KeyError, TypeError, ValueError):
            bbox = None
        for page_index in scope.get("page_indices", []):
            if isinstance(page_index, int):
                result.setdefault(page_index, []).append(bbox)
    return result


def _within_scope(bbox: BBox, scopes: list[BBox | None]) -> bool:
    return any(scope is None or _intersection_over_left(bbox, scope) > 0 for scope in scopes)


def _item_sequence(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    by_ref: dict[str, tuple[str, dict[str, Any]]] = {}
    fallback: list[tuple[str, dict[str, Any]]] = []
    for collection in _ITEM_COLLECTIONS:
        values = document.get(collection, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            record = (collection, item)
            fallback.append(record)
            self_ref = item.get("self_ref")
            if isinstance(self_ref, str):
                by_ref[self_ref] = record
    groups = document.get("groups", [])
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("self_ref"), str):
                by_ref[group["self_ref"]] = ("groups", group)
    ordered: list[tuple[str, dict[str, Any]]] = []
    visited: set[str] = set()

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        ref = node.get("$ref") or node.get("cref") or node.get("ref")
        if not isinstance(ref, str) or ref in visited:
            return
        visited.add(ref)
        record = by_ref.get(ref)
        if record is None:
            return
        collection, item = record
        if collection == "groups":
            for child in item.get("children", []):
                visit(child)
        else:
            ordered.append(record)

    body = document.get("body")
    if isinstance(body, dict):
        for child in body.get("children", []):
            visit(child)
    for record in fallback:
        self_ref = record[1].get("self_ref")
        if not isinstance(self_ref, str) or self_ref not in visited:
            ordered.append(record)
    return ordered


def _best_native_element(
    document: PhysicalDocument,
    page_index: int,
    bbox: BBox,
    label: str,
    text: str | None,
) -> tuple[str | None, float, float]:
    if label == "picture":
        kinds = {"image"}
    elif label == "table":
        kinds = {"image", "vector"}
    else:
        kinds = {"text"}
    tokens = set(re.findall(r"\w+", (text or "").casefold()))
    scored: list[tuple[float, float, float, str]] = []
    for element in document.pages[page_index].elements:
        if element.kind not in kinds:
            continue
        geometry = _intersection_over_left(bbox, element.bbox)
        if geometry <= 0:
            continue
        candidate_tokens = set(re.findall(r"\w+", (element.text or "").casefold()))
        union = tokens | candidate_tokens
        text_score = len(tokens & candidate_tokens) / len(union) if union else 0.0
        score = geometry * 0.7 + text_score * 0.3
        scored.append((score, geometry, text_score, element.element_id))
    if not scored:
        return None, 0.0, 0.0
    score, geometry, text_score, element_id = max(scored)
    threshold = 0.35 if kinds == {"text"} else 0.70
    return (
        (element_id, geometry, text_score)
        if score >= threshold
        else (None, geometry, text_score)
    )


def _stable_suffix(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def build_docling_evidence_from_documents(
    documents: list[dict[str, Any]],
    document: PhysicalDocument,
    requests: list[dict[str, Any]],
    *,
    provider_version: str = "fixture",
    diagnostics: list[dict[str, Any]] | None = None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
]:
    """Adapt exported DoclingDocument dictionaries into selected evidence."""

    scopes = _scopes_by_page(requests)
    observations_by_page: dict[int, list[dict[str, Any]]] = {
        page.page_index: [] for page in document.pages
    }
    raw_items: list[dict[str, Any]] = []
    alignments: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    observed_request_pages: set[int] = set()
    for document_index, exported in enumerate(documents):
        for item_sequence, (collection, item) in enumerate(_item_sequence(exported)):
            label = str(item.get("label") or collection.rstrip("s"))
            text = item.get("text")
            if text is not None:
                text = str(text)
            self_ref = str(item.get("self_ref") or f"/{collection}/{item_sequence}")
            selected_provenance: list[dict[str, Any]] = []
            evidence_ids: list[str] = []
            selected_pages: set[int] = set()
            for provenance_index, provenance in enumerate(item.get("prov", [])):
                if not isinstance(provenance, dict):
                    continue
                page_no = provenance.get("page_no")
                if not isinstance(page_no, int):
                    continue
                page_index = page_no - 1
                if page_index not in scopes or not 0 <= page_index < len(document.pages):
                    continue
                page = document.pages[page_index]
                converted = _bbox_from_provenance(
                    provenance,
                    page.width,
                    page.height,
                )
                if converted is None:
                    continue
                bbox, raw_bbox = converted
                if not _within_scope(bbox, scopes[page_index]):
                    continue
                suffix = _stable_suffix(
                    str(document_index), self_ref, str(provenance_index), str(page_index)
                )
                observation_id = f"{DOCLING_PROVIDER_ID}:{suffix}"
                physical_id, geometry, text_score = _best_native_element(
                    document,
                    page_index,
                    bbox,
                    label,
                    text,
                )
                observation = {
                    "observation_id": observation_id,
                    "physical_element_id": physical_id,
                    "kind": f"docling_{label}",
                    "provider_coordinate_system": "top-left/pdf-point/y-down",
                    "provider_bbox": {
                        "x0": bbox.x,
                        "y0": bbox.y,
                        "x1": bbox.right,
                        "y1": bbox.bottom,
                    },
                    "paperwright_bbox": bbox.to_dict(),
                    "text": text,
                    "text_fingerprint": text_fingerprint(text),
                    "source_ref": f"docling:{self_ref}:prov:{provenance_index}",
                    "extraction_method": "docling_document_json_provenance",
                    "materialization_status": "not_applicable",
                    "docling_provenance": {
                        "page_no": page_no,
                        "bbox": raw_bbox,
                        "charspan": provenance.get("charspan"),
                    },
                }
                observations_by_page[page_index].append(observation)
                selected_provenance.append(observation["docling_provenance"])
                evidence_ids.append(observation_id)
                selected_pages.add(page_index)
                observed_request_pages.add(page_index)
                if physical_id is not None:
                    alignments.append(
                        {
                            "alignment_id": f"align-{_stable_suffix(observation_id)}",
                            "provider_id": DOCLING_PROVIDER_ID,
                            "observation_id": observation_id,
                            "physical_element_id": physical_id,
                            "match_basis": "docling_provenance_geometry_and_text",
                            "algorithm_version": DOCLING_ALIGNMENT_VERSION,
                            "text_score": round(text_score, 6) if text else None,
                            "geometry_score": round(min(1.0, geometry), 6),
                        }
                    )
            if not evidence_ids:
                continue
            raw_item = {
                "collection": collection,
                "self_ref": self_ref,
                "label": label,
                "text_fingerprint": text_fingerprint(text),
                "provenance": selected_provenance,
            }
            if collection == "tables" and isinstance(item.get("data"), dict):
                raw_item["table_data"] = item["data"]
            raw_items.append(raw_item)
            role = _ROLE_BY_LABEL.get(label)
            if role is not None:
                boxes = [
                    BBox.from_dict(
                        next(
                            obs["paperwright_bbox"]
                            for obs in observations_by_page[page_index]
                            if obs["observation_id"] == observation_id
                        )
                    )
                    for page_index in sorted(selected_pages)
                    for observation_id in evidence_ids
                    if any(
                        obs["observation_id"] == observation_id
                        for obs in observations_by_page[page_index]
                    )
                ]
                left = min(box.x for box in boxes)
                top = min(box.y for box in boxes)
                right = max(box.right for box in boxes)
                bottom = max(box.bottom for box in boxes)
                claims.append(
                    {
                        "claim_id": "docling-role-"
                        + _stable_suffix(
                            self_ref,
                            role,
                            ",".join(map(str, sorted(selected_pages))),
                        ),
                        "provider_id": DOCLING_PROVIDER_ID,
                        "capability": (
                            "table_structure" if role == "table_region" else "layout_semantic_roles"
                        ),
                        "claim_type": role,
                        "evidence_observation_ids": evidence_ids,
                        "payload": {
                            "page_indices": sorted(selected_pages),
                            "paperwright_bbox": BBox(
                                left, top, right - left, bottom - top
                            ).to_dict(),
                            "direct_text_authority": False,
                            "direct_markdown_authority": False,
                        },
                        "status": "proposed",
                    }
                )
    for page_index in sorted(scopes):
        page_observations = observations_by_page.get(page_index, [])
        if len(page_observations) < 2:
            continue
        evidence_ids = [item["observation_id"] for item in page_observations]
        claims.append(
            {
                "claim_id": f"docling-reading-order-p{page_index:04d}",
                "provider_id": DOCLING_PROVIDER_ID,
                "capability": "reading_order",
                "claim_type": "reading_order",
                "evidence_observation_ids": evidence_ids,
                "payload": {
                    "page_index": page_index,
                    "ordered_observation_ids": evidence_ids,
                    "direct_text_authority": False,
                },
                "status": "proposed",
            }
        )
    pages = [
        {
            "page_index": page.page_index,
            "width": page.width,
            "height": page.height,
            "rotation": page.rotation,
            "provider_to_paperwright_affine": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            "observations": observations_by_page[page.page_index],
        }
        for page in document.pages
    ]
    observation_count = sum(len(page["observations"]) for page in pages)
    status = "complete" if observation_count and not diagnostics else "degraded"
    snapshot = {
        "contract_version": "paperwright-provider-snapshot-v0.1",
        "provider_id": DOCLING_PROVIDER_ID,
        "provider_version": provider_version,
        "source_sha256": document.source_sha256,
        "status": status,
        "capabilities": [
            "layout_semantic_roles",
            "reading_order",
            "table_structure",
            "visual_object_detection",
        ],
        "missing_capabilities": [] if status == "complete" else ["requested_pages_complete"],
        "diagnostics": diagnostics or [],
        "page_count": len(pages),
        "observation_count": observation_count,
        "pages": pages,
        "docling_document_subset": {
            "selected_only": True,
            "requested_page_indices": sorted(scopes),
            "items": raw_items,
        },
    }
    request_status = {
        str(item["request_id"]): (
            "completed"
            if set(item["scope"]["page_indices"]).issubset(observed_request_pages)
            else "failed"
        )
        for item in requests
    }
    return snapshot, alignments, claims, request_status


def build_docling_evidence(
    source: Path,
    document: PhysicalDocument,
    requests: list[dict[str, Any]],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
]:
    if not requests:
        snapshot = unavailable_docling_snapshot(
            document.source_sha256,
            reason="docling_not_requested_no_conflicts",
            requests=requests,
        )
        return snapshot, [], [], {}
    if os.environ.get("PAPERWRIGHT_DOCLING_ENABLED", "").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        snapshot = unavailable_docling_snapshot(
            document.source_sha256,
            reason="PAPERWRIGHT_DOCLING_ENABLED_not_set",
            requests=requests,
        )
        return snapshot, [], [], {
            str(item["request_id"]): "not_run" for item in requests
        }
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        snapshot = unavailable_docling_snapshot(
            document.source_sha256,
            reason="docling_dependency_not_installed",
            requests=requests,
        )
        return snapshot, [], [], {
            str(item["request_id"]): "not_run" for item in requests
        }
    version = importlib.metadata.version("docling")
    converter = DocumentConverter()
    documents: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    requested_pages = sorted(
        {
            page_index
            for item in requests
            for page_index in item["scope"]["page_indices"]
        }
    )
    for page_index in requested_pages:
        try:
            result = converter.convert(
                str(source),
                page_range=(page_index + 1, page_index + 1),
                max_num_pages=1,
            )
            documents.append(result.document.export_to_dict())
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "docling_page_conversion_failed",
                    "page_index": page_index,
                    "error_type": type(exc).__name__,
                }
            )
    if not documents:
        snapshot = unavailable_docling_snapshot(
            document.source_sha256,
            reason="docling_all_requested_pages_failed",
            requests=requests,
        )
        snapshot["provider_version"] = version
        return snapshot, [], [], {
            str(item["request_id"]): "failed" for item in requests
        }
    return build_docling_evidence_from_documents(
        documents,
        document,
        requests,
        provider_version=version,
        diagnostics=diagnostics,
    )
