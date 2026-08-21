"""Deterministic routing of unresolved source-evidence conflicts.

The router is deliberately provider-agnostic except for capability names.  It
turns disagreements already visible in immutable observations/claims into
page/ROI-scoped specialist requests; it never asks a specialist to rewrite a
whole paper.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .models import BBox, PhysicalDocument

SPECIALIST_REQUESTS_VERSION = "paperwright-specialist-requests-v0.1"
DOCLING_PROVIDER_ID = "docling-local"


def _bbox(value: object) -> BBox | None:
    if not isinstance(value, dict):
        return None
    try:
        return BBox.from_dict(value)
    except (KeyError, TypeError, ValueError):
        return None


def _union(boxes: list[BBox]) -> dict[str, float] | None:
    if not boxes:
        return None
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return BBox(left, top, right - left, bottom - top).to_dict()


def _intersection_over_left(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height / max(left.width * left.height, 1e-9)


def _request_id(conflict_id: str) -> str:
    digest = hashlib.sha256(conflict_id.encode("utf-8")).hexdigest()[:20]
    return f"specialist-{digest}"


def _request(
    conflict: dict[str, Any],
    *,
    page_indices: list[int],
    bbox: dict[str, float] | None,
    capabilities: list[str],
) -> dict[str, Any]:
    request_id = _request_id(str(conflict["conflict_id"]))
    conflict["specialist_request_id"] = request_id
    return {
        "request_id": request_id,
        "provider_id": DOCLING_PROVIDER_ID,
        "conflict_id": conflict["conflict_id"],
        "scope": {
            "page_indices": sorted(set(page_indices)),
            "paperwright_bbox": bbox,
        },
        "requested_capabilities": sorted(set(capabilities)),
        "status": "requested",
    }


def _observations_by_id(
    snapshots: Mapping[str, dict[str, Any]],
) -> dict[str, tuple[int, dict[str, Any]]]:
    result: dict[str, tuple[int, dict[str, Any]]] = {}
    for snapshot in snapshots.values():
        for page in snapshot.get("pages", []):
            page_index = page.get("page_index")
            if not isinstance(page_index, int):
                continue
            for observation in page.get("observations", []):
                observation_id = observation.get("observation_id")
                if isinstance(observation_id, str):
                    result[observation_id] = (page_index, observation)
    return result


def _claim_box(
    claim: dict[str, Any],
    observations: Mapping[str, tuple[int, dict[str, Any]]],
) -> tuple[int | None, BBox | None]:
    payload = claim.get("payload")
    if isinstance(payload, dict):
        box = _bbox(payload.get("paperwright_bbox"))
        page_index = payload.get("page_index")
        if box is not None and isinstance(page_index, int):
            return page_index, box
    pages: set[int] = set()
    boxes: list[BBox] = []
    for observation_id in claim.get("evidence_observation_ids", []):
        record = observations.get(observation_id)
        if record is None:
            continue
        page_index, observation = record
        box = _bbox(observation.get("paperwright_bbox"))
        if box is not None:
            pages.add(page_index)
            boxes.append(box)
    if len(pages) != 1:
        return None, None
    union = _union(boxes)
    return next(iter(pages)), _bbox(union)


def _table_conflicts(
    claims: list[dict[str, Any]],
    observations: Mapping[str, tuple[int, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    semantic_tables = [
        claim
        for claim in claims
        if claim.get("provider_id") == "grobid-scholarly"
        and claim.get("claim_type") in {"table", "table_caption"}
    ]
    for claim in claims:
        if (
            claim.get("provider_id") != "pdfplumber-geometry"
            or claim.get("claim_type") != "table_region"
        ):
            continue
        page_index, table_box = _claim_box(claim, observations)
        if page_index is None or table_box is None:
            continue
        corroborating_claim_ids: list[str] = []
        for semantic in semantic_tables:
            semantic_page, semantic_box = _claim_box(semantic, observations)
            if (
                semantic_page == page_index
                and semantic_box is not None
                and (
                    _intersection_over_left(table_box, semantic_box) >= 0.15
                    or _intersection_over_left(semantic_box, table_box) >= 0.15
                )
            ):
                corroborating_claim_ids.append(str(semantic["claim_id"]))
        if corroborating_claim_ids:
            continue
        conflict_id = f"conflict-table-p{page_index:04d}-{claim['claim_id']}"
        conflict = {
            "conflict_id": conflict_id,
            "kind": "table_boundary_or_structure",
            "observation_ids": list(claim["evidence_observation_ids"]),
            "claim_ids": [str(claim["claim_id"])],
            "status": "open",
            "page_index": page_index,
            "paperwright_bbox": table_box.to_dict(),
            "reasons": ["single_provider_table_proposal"],
        }
        conflicts.append(conflict)
        requests.append(
            _request(
                conflict,
                page_indices=[page_index],
                bbox=table_box.to_dict(),
                capabilities=["layout_semantic_roles", "table_structure"],
            )
        )
    return conflicts, requests


def _reading_order_conflicts(
    document: PhysicalDocument,
    snapshots: Mapping[str, dict[str, Any]],
    claims: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = snapshots.get("grobid-scholarly")
    if snapshot is None or snapshot.get("status") not in {"complete", "degraded"}:
        return [], []
    claims_by_observation: dict[str, list[str]] = {}
    for claim in claims:
        if claim.get("provider_id") != "grobid-scholarly":
            continue
        for observation_id in claim.get("evidence_observation_ids", []):
            claims_by_observation.setdefault(observation_id, []).append(
                str(claim["claim_id"])
            )
    conflicts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for page_record in snapshot.get("pages", []):
        page_index = page_record.get("page_index")
        if not isinstance(page_index, int) or not 0 <= page_index < len(document.pages):
            continue
        canonical_order = {
            element.element_id: sequence
            for sequence, element in enumerate(document.pages[page_index].elements)
            if element.kind == "text"
        }
        ordered: list[tuple[str, str, BBox]] = []
        seen: set[str] = set()
        for observation in page_record.get("observations", []):
            physical_id = observation.get("physical_element_id")
            observation_id = observation.get("observation_id")
            box = _bbox(observation.get("paperwright_bbox"))
            if (
                isinstance(physical_id, str)
                and physical_id in canonical_order
                and physical_id not in seen
                and isinstance(observation_id, str)
                and box is not None
            ):
                ordered.append((physical_id, observation_id, box))
                seen.add(physical_id)
        if len(ordered) < 4:
            continue
        sequence = [canonical_order[item[0]] for item in ordered]
        inversions = sum(
            1
            for left in range(len(sequence))
            for right in range(left + 1, len(sequence))
            if sequence[left] > sequence[right]
        )
        pairs = len(sequence) * (len(sequence) - 1) // 2
        rate = inversions / pairs
        if inversions < 3 or rate < 0.25:
            continue
        observation_ids = [item[1] for item in ordered]
        claim_ids = sorted(
            {
                claim_id
                for observation_id in observation_ids
                for claim_id in claims_by_observation.get(observation_id, [])
            }
        )
        region = _union([item[2] for item in ordered])
        conflict_id = f"conflict-reading-order-p{page_index:04d}"
        conflict = {
            "conflict_id": conflict_id,
            "kind": "multi_provider_reading_order",
            "observation_ids": observation_ids,
            "claim_ids": claim_ids,
            "status": "open",
            "page_index": page_index,
            "paperwright_bbox": region,
            "reasons": ["grobid_order_differs_from_pdfium_native_order"],
            "metrics": {
                "aligned_item_count": len(sequence),
                "inversion_count": inversions,
                "inversion_rate": round(rate, 6),
            },
        }
        conflicts.append(conflict)
        requests.append(
            _request(
                conflict,
                page_indices=[page_index],
                bbox=region,
                capabilities=["layout_semantic_roles", "reading_order"],
            )
        )
    return conflicts, requests


def _raster_value(analysis: object, name: str) -> float | None:
    value = getattr(analysis, name, None)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(analysis, dict):
        coverage = analysis.get("coverage")
        if isinstance(coverage, dict) and isinstance(coverage.get(name), (int, float)):
            return float(coverage[name])
    return None


def _object_raster_conflicts(
    document: PhysicalDocument,
    snapshots: Mapping[str, dict[str, Any]],
    raster_analyses: Mapping[int, object] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not raster_analyses:
        return [], []
    pdfium = snapshots.get("pdfium-native")
    if pdfium is None:
        return [], []
    conflicts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for page_record in pdfium.get("pages", []):
        page_index = page_record.get("page_index")
        if not isinstance(page_index, int) or page_index not in raster_analyses:
            continue
        residual = _raster_value(raster_analyses[page_index], "residual")
        if residual is None:
            residual = _raster_value(raster_analyses[page_index], "residual_coverage")
        if residual is None:
            continue
        image_observations = [
            item for item in page_record.get("observations", []) if item.get("kind") == "image"
        ]
        vector_observations = [
            item for item in page_record.get("observations", []) if item.get("kind") == "vector"
        ]
        image_boxes = [
            box
            for item in image_observations
            if (box := _bbox(item.get("paperwright_bbox"))) is not None
        ]
        page = document.pages[page_index]
        image_area_ratio = min(
            1.0,
            sum(box.width * box.height for box in image_boxes)
            / max(page.width * page.height, 1e-9),
        )
        reason: str | None = None
        observation_ids: list[str] = []
        region: dict[str, float] | None = None
        if image_area_ratio >= 0.08 and residual <= 0.001:
            reason = "native_image_area_without_raster_residual"
            observation_ids = [str(item["observation_id"]) for item in image_observations]
            region = _union(image_boxes)
        elif residual >= 0.08 and not image_observations and not vector_observations:
            reason = "large_raster_residual_without_native_visual_objects"
            region = BBox(0.0, 0.0, page.width, page.height).to_dict()
        if reason is None:
            continue
        conflict_id = f"conflict-native-raster-p{page_index:04d}"
        conflict = {
            "conflict_id": conflict_id,
            "kind": "native_object_raster_mismatch",
            "observation_ids": observation_ids,
            "claim_ids": [],
            "status": "open",
            "page_index": page_index,
            "paperwright_bbox": region,
            "reasons": [reason],
            "metrics": {
                "native_image_area_ratio": round(image_area_ratio, 6),
                "raster_residual_coverage": round(residual, 6),
            },
        }
        conflicts.append(conflict)
        requests.append(
            _request(
                conflict,
                page_indices=[page_index],
                bbox=region,
                capabilities=["layout_semantic_roles", "visual_object_detection"],
            )
        )
    return conflicts, requests


def derive_specialist_requests(
    document: PhysicalDocument,
    snapshots: Mapping[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    *,
    raster_analyses: Mapping[int, object] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return deterministic conflicts and page/ROI-scoped Docling requests."""

    observations = _observations_by_id(snapshots)
    conflict_groups = [
        _table_conflicts(claims, observations),
        _reading_order_conflicts(document, snapshots, claims),
        _object_raster_conflicts(document, snapshots, raster_analyses),
    ]
    conflicts = [item for group, _ in conflict_groups for item in group]
    requests = [item for _, group in conflict_groups for item in group]
    conflicts.sort(key=lambda item: str(item["conflict_id"]))
    requests.sort(key=lambda item: str(item["request_id"]))
    return conflicts, requests
