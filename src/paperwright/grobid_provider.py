"""Optional local GROBID scholarly-structure evidence provider."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any
from urllib import request
import xml.etree.ElementTree as ET

from .models import BBox, PhysicalDocument
from .source_evidence import text_fingerprint

GROBID_PROVIDER_ID = "grobid-scholarly"
GROBID_ALIGNMENT_VERSION = "paperwright-grobid-native-text-alignment-v0.1"
_COORDINATE_ROLES = {
    "persName": "author",
    "affiliation": "affiliation",
    "orgName": "affiliation",
    "head": "section_heading",
    "p": "paragraph",
    "figDesc": "figure_caption",
    "biblStruct": "reference",
    "ref": "inline_citation",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalized_text(node: ET.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _coords(value: str | None) -> tuple[tuple[int, float, float, float, float], ...]:
    result = []
    for part in (value or "").split(";"):
        fields = part.split(",")
        if len(fields) != 5:
            continue
        try:
            page, x, y, width, height = (
                int(fields[0]),
                float(fields[1]),
                float(fields[2]),
                float(fields[3]),
                float(fields[4]),
            )
        except ValueError:
            continue
        if page > 0 and width > 0 and height > 0:
            result.append((page - 1, x, y, width, height))
    return tuple(result)


def _role(
    node: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> str | None:
    name = _local_name(node.tag)
    ancestors: list[ET.Element] = []
    parent = parents.get(node)
    while parent is not None:
        ancestors.append(parent)
        parent = parents.get(parent)
    ancestor_names = {_local_name(item.tag) for item in ancestors}
    if name == "title":
        return "title" if "titleStmt" in ancestor_names else None
    if name == "p" and "abstract" in ancestor_names:
        return "abstract"
    if name == "head" and "figure" in ancestor_names:
        figure = next(item for item in ancestors if _local_name(item.tag) == "figure")
        return "table_caption" if figure.get("type") == "table" else "figure_caption"
    role = _COORDINATE_ROLES.get(name)
    if name == "figure" and node.get("type") == "table":
        return "table"
    return role


def _overlap_ratio(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height / max(left.width * left.height, 1e-9)


def _best_text_element(
    document: PhysicalDocument,
    page_index: int,
    bbox: BBox,
    text: str,
) -> tuple[str | None, float, float]:
    tokens = set(re.findall(r"\w+", text.casefold()))
    scored: list[tuple[float, float, float, str]] = []
    for element in document.pages[page_index].elements:
        if element.kind != "text" or not element.text:
            continue
        geometry = _overlap_ratio(bbox, element.bbox)
        if geometry <= 0:
            continue
        candidate_tokens = set(re.findall(r"\w+", element.text.casefold()))
        union = tokens | candidate_tokens
        text_score = len(tokens & candidate_tokens) / len(union) if union else 0.0
        score = geometry * 0.55 + text_score * 0.45
        scored.append((score, geometry, text_score, element.element_id))
    if not scored:
        return None, 0.0, 0.0
    score, geometry, text_score, element_id = max(scored)
    return (element_id, geometry, text_score) if score >= 0.35 else (None, geometry, text_score)


def unavailable_grobid_snapshot(
    source_sha256: str,
    reason: str = "PAPERWRIGHT_GROBID_URL_not_configured",
) -> dict[str, Any]:
    return {
        "contract_version": "paperwright-provider-snapshot-v0.1",
        "provider_id": GROBID_PROVIDER_ID,
        "provider_version": "unavailable",
        "source_sha256": source_sha256,
        "status": "unavailable",
        "capabilities": [],
        "missing_capabilities": [
            "citation_links",
            "scholarly_semantic_roles",
            "tei_coordinates",
        ],
        "diagnostics": [{"code": reason}],
        "page_count": 0,
        "observation_count": 0,
        "pages": [],
    }


def build_grobid_evidence_from_tei(
    tei: bytes | str,
    document: PhysicalDocument,
    *,
    provider_version: str = "unknown",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse coordinate-bearing TEI into observations and proposed roles."""

    root = ET.fromstring(tei)
    parents = {child: parent for parent in root.iter() for child in parent}
    observations_by_page: dict[int, list[dict[str, Any]]] = {
        page.page_index: [] for page in document.pages
    }
    alignments: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    sequence = 0
    for node in root.iter():
        role = _role(node, parents)
        if role is None:
            continue
        text = _normalized_text(node)
        coordinates = _coords(node.get("coords"))
        if not text or not coordinates:
            continue
        evidence_ids: list[str] = []
        pages_for_claim: set[int] = set()
        for segment, (page_index, x, y, width, height) in enumerate(coordinates):
            if page_index not in observations_by_page:
                diagnostics.append(
                    {"code": "coordinate_page_out_of_range", "role": role}
                )
                continue
            page = document.pages[page_index]
            if x < 0 or y < 0 or x + width > page.width + 1e-3 or y + height > page.height + 1e-3:
                diagnostics.append(
                    {"code": "coordinate_bbox_out_of_range", "page_index": page_index}
                )
                continue
            observation_id = f"{GROBID_PROVIDER_ID}:n{sequence:06d}:s{segment:03d}"
            bbox = BBox(x, y, width, height)
            physical_id, geometry, text_score = _best_text_element(
                document, page_index, bbox, text
            )
            observations_by_page[page_index].append(
                {
                    "observation_id": observation_id,
                    "physical_element_id": physical_id,
                    "kind": "tei_span",
                    "provider_coordinate_system": "top-left/pdf-point/y-down",
                    "provider_bbox": {"x0": x, "y0": y, "x1": x + width, "y1": y + height},
                    "paperwright_bbox": bbox.to_dict(),
                    "text": text,
                    "text_fingerprint": text_fingerprint(text),
                    "source_ref": f"tei-node:{sequence}:segment:{segment}",
                    "extraction_method": "grobid_tei_coordinates",
                    "materialization_status": "not_applicable",
                }
            )
            evidence_ids.append(observation_id)
            pages_for_claim.add(page_index)
            if physical_id is not None:
                digest = hashlib.sha256(observation_id.encode()).hexdigest()[:20]
                alignments.append(
                    {
                        "alignment_id": f"align-{digest}",
                        "provider_id": GROBID_PROVIDER_ID,
                        "observation_id": observation_id,
                        "physical_element_id": physical_id,
                        "match_basis": "tei_coordinate_text_overlap",
                        "algorithm_version": GROBID_ALIGNMENT_VERSION,
                        "text_score": round(text_score, 6),
                        "geometry_score": round(min(1.0, geometry), 6),
                    }
                )
        if evidence_ids:
            claims.append(
                {
                    "claim_id": f"grobid-role-{sequence:06d}",
                    "provider_id": GROBID_PROVIDER_ID,
                    "capability": "scholarly_semantic_roles",
                    "claim_type": role,
                    "evidence_observation_ids": evidence_ids,
                    "payload": {
                        "page_indices": sorted(pages_for_claim),
                        "text_fingerprint": text_fingerprint(text),
                        "direct_text_authority": False,
                    },
                    "status": "proposed",
                }
            )
        sequence += 1
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
    count = sum(len(page["observations"]) for page in pages)
    snapshot = {
        "contract_version": "paperwright-provider-snapshot-v0.1",
        "provider_id": GROBID_PROVIDER_ID,
        "provider_version": provider_version,
        "source_sha256": document.source_sha256,
        "status": "complete" if count else "degraded",
        "capabilities": ["citation_links", "scholarly_semantic_roles", "tei_coordinates"],
        "missing_capabilities": [] if count else ["coordinate_bearing_semantic_nodes"],
        "diagnostics": diagnostics,
        "page_count": len(pages),
        "observation_count": count,
        "pages": pages,
    }
    return snapshot, alignments, claims


def _request_tei(source: Path, base_url: str) -> tuple[bytes, str]:
    boundary = "----paperwright-grobid-v01"
    data = source.read_bytes()
    body = bytearray((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"input\"; filename=\"paper.pdf\"\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode())
    body.extend(data)
    for coordinate in (
        "persName",
        "affiliation",
        "orgName",
        "ref",
        "biblStruct",
        "formula",
        "figure",
        "head",
        "p",
        "s",
        "note",
        "title",
    ):
        body.extend(
            (
                f"\r\n--{boundary}\r\n"
                "Content-Disposition: form-data; name=\"teiCoordinates\"\r\n\r\n"
                f"{coordinate}"
            ).encode()
        )
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    endpoint = base_url.rstrip("/") + "/api/processFulltextDocument"
    req = request.Request(
        endpoint,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as response:
        return response.read(), response.headers.get("Grobid-Version", "http-api")


def build_grobid_evidence(
    source: Path,
    document: PhysicalDocument,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    base_url = os.environ.get("PAPERWRIGHT_GROBID_URL", "").strip()
    if not base_url:
        return unavailable_grobid_snapshot(document.source_sha256), [], []
    try:
        tei, version = _request_tei(source, base_url)
        return build_grobid_evidence_from_tei(
            tei,
            document,
            provider_version=version,
        )
    except Exception as exc:
        return unavailable_grobid_snapshot(
            document.source_sha256,
            reason=f"grobid_request_failed:{type(exc).__name__}",
        ), [], []
