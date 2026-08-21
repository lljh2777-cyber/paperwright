"""Immutable multi-provider source evidence contracts.

E1 starts with a lossless PDFium snapshot adapted from PhysicalDocument.  The
contract deliberately separates provider observations, alignments, claims and
conflicts so later sidecars never overwrite native evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import unicodedata
from typing import Any

from .exceptions import ContractValidationError
from .models import Element, PhysicalDocument

SOURCE_EVIDENCE_VERSION = "paperwright-source-evidence-v0.1"
PROVIDER_SNAPSHOT_VERSION = "paperwright-provider-snapshot-v0.1"
ALIGNMENTS_VERSION = "paperwright-observation-alignments-v0.1"
CLAIMS_VERSION = "paperwright-source-claims-v0.1"
CONFLICTS_VERSION = "paperwright-source-conflicts-v0.1"
PDFIUM_PROVIDER_ID = "pdfium-native"
ALIGNMENT_ALGORITHM_VERSION = "paperwright-pdfium-source-ref-alignment-v0.1"


def _canonical_json(value: dict[str, Any]) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def text_fingerprint(text: str | None) -> str | None:
    """Hash normalized visible text for cross-provider alignment."""

    if text is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    return _sha256_bytes(normalized.encode("utf-8"))


def _provider_bbox(element: Element, page_height: float) -> dict[str, float]:
    return {
        "x0": element.bbox.x,
        "y0": page_height - element.bbox.bottom,
        "x1": element.bbox.right,
        "y1": page_height - element.bbox.y,
    }


def _observation_id(element: Element) -> str:
    return f"{PDFIUM_PROVIDER_ID}:{element.element_id}"


def _alignment_id(observation_id: str) -> str:
    digest = _sha256_bytes(observation_id.encode("utf-8"))[:20]
    return f"align-{digest}"


def build_pdfium_source_evidence(
    document: PhysicalDocument,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build a source-evidence index and its four immutable artifacts."""

    inventory = str(document.metadata.get("native_object_inventory", ""))
    has_object_inventory = not inventory.startswith("text_only;")
    capabilities = ["native_text", "render"]
    missing_capabilities: list[str] = []
    if has_object_inventory:
        capabilities.append("object_inventory")
    else:
        missing_capabilities.append("object_inventory")
    if document.metadata.get("extraction_profile") in {"full", "hybrid-standard"}:
        capabilities.append("embedded_image_materialization")
    else:
        missing_capabilities.append("embedded_image_materialization")

    pages: list[dict[str, Any]] = []
    alignments: list[dict[str, Any]] = []
    observation_count = 0
    for page in document.pages:
        observations: list[dict[str, Any]] = []
        for element in page.elements:
            observation_id = _observation_id(element)
            fingerprint = text_fingerprint(element.text)
            observation = {
                "observation_id": observation_id,
                "physical_element_id": element.element_id,
                "kind": element.kind,
                "provider_coordinate_system": "bottom-left/pdf-point/y-up",
                "provider_bbox": _provider_bbox(element, page.height),
                "paperwright_bbox": element.bbox.to_dict(),
                "text": element.text,
                "text_fingerprint": fingerprint,
                "source_ref": element.provenance.source_ref,
                "extraction_method": element.provenance.method,
                "materialization_status": element.metadata.get(
                    "asset_materialization",
                    "not_applicable",
                ),
            }
            observations.append(observation)
            alignments.append(
                {
                    "alignment_id": _alignment_id(observation_id),
                    "provider_id": PDFIUM_PROVIDER_ID,
                    "observation_id": observation_id,
                    "physical_element_id": element.element_id,
                    "match_basis": "shared_pdfium_source_ref_and_element_id",
                    "algorithm_version": ALIGNMENT_ALGORITHM_VERSION,
                    "text_score": 1.0 if element.text is not None else None,
                    "geometry_score": 1.0,
                }
            )
            observation_count += 1
        pages.append(
            {
                "page_index": page.page_index,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
                "provider_to_paperwright_affine": [
                    1.0,
                    0.0,
                    0.0,
                    -1.0,
                    0.0,
                    page.height,
                ],
                "observations": observations,
            }
        )

    snapshot = {
        "contract_version": PROVIDER_SNAPSHOT_VERSION,
        "provider_id": PDFIUM_PROVIDER_ID,
        "provider_version": document.backend_version,
        "source_sha256": document.source_sha256,
        "physical_document_sha256": document.deterministic_sha256(),
        "status": "complete" if has_object_inventory else "degraded",
        "capabilities": sorted(capabilities),
        "missing_capabilities": sorted(set(missing_capabilities)),
        "page_count": len(pages),
        "observation_count": observation_count,
        "pages": pages,
    }
    alignment_document = {
        "contract_version": ALIGNMENTS_VERSION,
        "source_sha256": document.source_sha256,
        "alignments": alignments,
    }
    claims = {
        "contract_version": CLAIMS_VERSION,
        "source_sha256": document.source_sha256,
        "claims": [],
    }
    conflicts = {
        "contract_version": CONFLICTS_VERSION,
        "source_sha256": document.source_sha256,
        "conflicts": [],
    }
    artifacts = {
        "providers/pdfium-native.json": snapshot,
        "alignments.json": alignment_document,
        "claims.json": claims,
        "conflicts.json": conflicts,
    }
    provider_path = "providers/pdfium-native.json"
    index = {
        "contract_version": SOURCE_EVIDENCE_VERSION,
        "source_sha256": document.source_sha256,
        "providers": [
            {
                "provider_id": PDFIUM_PROVIDER_ID,
                "version": document.backend_version,
                "capabilities": sorted(capabilities),
                "missing_capabilities": sorted(set(missing_capabilities)),
                "snapshot_path": provider_path,
                "snapshot_sha256": _sha256_bytes(
                    _canonical_json(snapshot).encode("utf-8")
                ),
                "status": snapshot["status"],
            }
        ],
        "alignments_path": "alignments.json",
        "alignments_sha256": _sha256_bytes(
            _canonical_json(alignment_document).encode("utf-8")
        ),
        "claims_path": "claims.json",
        "claims_sha256": _sha256_bytes(
            _canonical_json(claims).encode("utf-8")
        ),
        "conflicts_path": "conflicts.json",
        "conflicts_sha256": _sha256_bytes(
            _canonical_json(conflicts).encode("utf-8")
        ),
        "summary": {
            "provider_count": 1,
            "observation_count": observation_count,
            "alignment_count": len(alignments),
            "claim_count": 0,
            "conflict_count": 0,
        },
    }
    return index, artifacts


def _safe_artifact_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ContractValidationError("source evidence artifact path 非法")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ContractValidationError("source evidence artifact path 越界") from exc
    return candidate


def _load_hashed_artifact(
    root: Path,
    relative: object,
    expected_sha256: object,
) -> dict[str, Any]:
    path = _safe_artifact_path(root, relative)
    if not path.is_file() or _sha256_file(path) != expected_sha256:
        raise ContractValidationError(f"source evidence artifact 哈希不匹配: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"source evidence artifact 无法解析: {relative}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("source evidence artifact 顶层必须是对象")
    return value


def _finite_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ContractValidationError(f"{label} 必须是有限数")
    return float(value)


def _transform_provider_bbox(
    bbox: dict[str, Any],
    affine: list[object],
) -> tuple[float, float, float, float]:
    if len(affine) != 6:
        raise ContractValidationError("provider affine 必须包含 6 个数")
    a, b, c, d, e, f = (
        _finite_number(value, "provider affine") for value in affine
    )
    x0 = _finite_number(bbox.get("x0"), "provider x0")
    y0 = _finite_number(bbox.get("y0"), "provider y0")
    x1 = _finite_number(bbox.get("x1"), "provider x1")
    y1 = _finite_number(bbox.get("y1"), "provider y1")
    if x1 <= x0 or y1 <= y0:
        raise ContractValidationError("provider bbox 必须具有正面积")
    points = tuple(
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))
    )
    left = min(point[0] for point in points)
    top = min(point[1] for point in points)
    right = max(point[0] for point in points)
    bottom = max(point[1] for point in points)
    return left, top, right - left, bottom - top


def validate_source_evidence_bundle(root: Path) -> dict[str, Any]:
    """Validate hashes, coordinate transforms and complete observation alignment."""

    root = Path(root).resolve()
    index_path = root / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError("source evidence index 无法解析") from exc
    if not isinstance(index, dict) or index.get("contract_version") != SOURCE_EVIDENCE_VERSION:
        raise ContractValidationError("source evidence index 契约版本不匹配")
    source_sha256 = index.get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ContractValidationError("source evidence source_sha256 非法")
    providers = index.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ContractValidationError("source evidence providers 不能为空")

    observation_by_id: dict[str, dict[str, Any]] = {}
    observation_provider_by_id: dict[str, str] = {}
    provider_ids: set[str] = set()
    pdfium_observation_ids: set[str] = set()
    canonical_physical_ids: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            raise ContractValidationError("source evidence provider 记录非法")
        provider_id = provider.get("provider_id")
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or provider_id in provider_ids
        ):
            raise ContractValidationError("source evidence provider_id 非法或重复")
        provider_ids.add(provider_id)
        capabilities = provider.get("capabilities")
        missing_capabilities = provider.get("missing_capabilities")
        if (
            not isinstance(provider.get("version"), str)
            or not provider["version"]
            or provider.get("status") not in {"complete", "degraded", "unavailable"}
            or not isinstance(capabilities, list)
            or not isinstance(missing_capabilities, list)
            or not all(isinstance(item, str) and item for item in capabilities)
            or not all(
                isinstance(item, str) and item for item in missing_capabilities
            )
            or len(set(capabilities)) != len(capabilities)
            or len(set(missing_capabilities)) != len(missing_capabilities)
            or set(capabilities) & set(missing_capabilities)
        ):
            raise ContractValidationError("source evidence provider 能力记录非法")
        snapshot = _load_hashed_artifact(
            root,
            provider.get("snapshot_path"),
            provider.get("snapshot_sha256"),
        )
        if (
            snapshot.get("contract_version") != PROVIDER_SNAPSHOT_VERSION
            or snapshot.get("provider_id") != provider.get("provider_id")
            or snapshot.get("provider_version") != provider.get("version")
            or snapshot.get("source_sha256") != source_sha256
            or snapshot.get("status") != provider.get("status")
            or snapshot.get("capabilities") != provider.get("capabilities")
            or snapshot.get("missing_capabilities")
            != provider.get("missing_capabilities")
        ):
            raise ContractValidationError("provider snapshot 与 index 不一致")
        pages = snapshot.get("pages")
        if not isinstance(pages, list) or snapshot.get("page_count") != len(pages):
            raise ContractValidationError("provider snapshot pages 非法")
        if [page.get("page_index") for page in pages if isinstance(page, dict)] != list(
            range(len(pages))
        ):
            raise ContractValidationError("provider snapshot page_index 不连续")
        snapshot_count = 0
        provider_physical_ids: set[str] = set()
        for page in pages:
            width = _finite_number(page.get("width"), "page width")
            height = _finite_number(page.get("height"), "page height")
            observations = page.get("observations")
            affine = page.get("provider_to_paperwright_affine")
            if (
                width <= 0
                or height <= 0
                or not isinstance(observations, list)
                or not isinstance(affine, list)
                or page.get("rotation") not in {0, 90, 180, 270}
            ):
                raise ContractValidationError("provider snapshot page 几何非法")
            for observation in observations:
                if not isinstance(observation, dict):
                    raise ContractValidationError("provider observation 非法")
                observation_id = observation.get("observation_id")
                physical_id = observation.get("physical_element_id")
                if (
                    not isinstance(observation_id, str)
                    or not observation_id
                    or observation_id in observation_by_id
                    or (
                        provider_id == PDFIUM_PROVIDER_ID
                        and (not isinstance(physical_id, str) or not physical_id)
                    )
                    or (
                        provider_id != PDFIUM_PROVIDER_ID
                        and physical_id is not None
                        and (not isinstance(physical_id, str) or not physical_id)
                    )
                    or (
                        provider_id == PDFIUM_PROVIDER_ID
                        and physical_id in provider_physical_ids
                    )
                ):
                    raise ContractValidationError("provider observation ID 非法或重复")
                paperwright = observation.get("paperwright_bbox")
                provider_bbox = observation.get("provider_bbox")
                mapping_bbox = observation.get(
                    "provider_bbox_for_mapping",
                    provider_bbox,
                )
                if (
                    not isinstance(paperwright, dict)
                    or not isinstance(provider_bbox, dict)
                    or not isinstance(mapping_bbox, dict)
                    or not isinstance(observation.get("kind"), str)
                    or not observation["kind"]
                    or not isinstance(
                        observation.get("provider_coordinate_system"), str
                    )
                    or not isinstance(observation.get("source_ref"), str)
                    or not isinstance(observation.get("extraction_method"), str)
                    or not isinstance(
                        observation.get("materialization_status"), str
                    )
                    or observation.get("text") is not None
                    and not isinstance(observation.get("text"), str)
                ):
                    raise ContractValidationError("provider observation bbox 缺失")
                x = _finite_number(paperwright.get("x"), "bbox x")
                y = _finite_number(paperwright.get("y"), "bbox y")
                box_width = _finite_number(paperwright.get("width"), "bbox width")
                box_height = _finite_number(paperwright.get("height"), "bbox height")
                mapped = _transform_provider_bbox(mapping_bbox, affine)
                expected = (x, y, box_width, box_height)
                if (
                    box_width <= 0
                    or box_height <= 0
                    or x < 0
                    or y < 0
                    or x + box_width > width + 1e-6
                    or y + box_height > height + 1e-6
                    or any(
                        abs(actual - target) > 1e-6
                        for actual, target in zip(mapped, expected, strict=True)
                    )
                ):
                    raise ContractValidationError("provider observation 坐标映射非法")
                if observation.get("text_fingerprint") != text_fingerprint(
                    observation.get("text")
                ):
                    raise ContractValidationError("provider observation 文本指纹不匹配")
                observation_by_id[observation_id] = observation
                observation_provider_by_id[observation_id] = provider_id
                if isinstance(physical_id, str):
                    provider_physical_ids.add(physical_id)
                if provider_id == PDFIUM_PROVIDER_ID:
                    pdfium_observation_ids.add(observation_id)
                    canonical_physical_ids.add(physical_id)
                snapshot_count += 1
        if snapshot.get("observation_count") != snapshot_count:
            raise ContractValidationError("provider observation_count 不匹配")

    if PDFIUM_PROVIDER_ID not in provider_ids:
        raise ContractValidationError("source evidence 缺少 PDFium 规范物理 provider")

    alignments_doc = _load_hashed_artifact(
        root,
        index.get("alignments_path"),
        index.get("alignments_sha256"),
    )
    if (
        alignments_doc.get("contract_version") != ALIGNMENTS_VERSION
        or alignments_doc.get("source_sha256") != source_sha256
        or not isinstance(alignments_doc.get("alignments"), list)
    ):
        raise ContractValidationError("source evidence alignments 非法")
    aligned_ids: set[str] = set()
    alignment_ids: set[str] = set()
    for alignment in alignments_doc["alignments"]:
        if not isinstance(alignment, dict):
            raise ContractValidationError("observation alignment 非法")
        observation_id = alignment.get("observation_id")
        alignment_id = alignment.get("alignment_id")
        observation = observation_by_id.get(observation_id)
        physical_element_id = alignment.get("physical_element_id")
        text_score = alignment.get("text_score")
        if (
            observation is None
            or observation_id in aligned_ids
            or not isinstance(alignment_id, str)
            or alignment_id in alignment_ids
            or alignment.get("provider_id")
            != observation_provider_by_id.get(observation_id)
            or physical_element_id not in canonical_physical_ids
            or (
                observation.get("physical_element_id") is not None
                and physical_element_id
                != observation.get("physical_element_id")
            )
            or not isinstance(alignment.get("algorithm_version"), str)
            or not isinstance(alignment.get("geometry_score"), (int, float))
            or not 0 <= float(alignment["geometry_score"]) <= 1
            or text_score is not None
            and (
                not isinstance(text_score, (int, float))
                or not 0 <= float(text_score) <= 1
            )
        ):
            raise ContractValidationError("observation alignment 不守恒")
        aligned_ids.add(observation_id)
        alignment_ids.add(alignment_id)
    if not pdfium_observation_ids.issubset(aligned_ids):
        raise ContractValidationError("PDFium observation alignment 覆盖不完整")

    claims = _load_hashed_artifact(
        root,
        index.get("claims_path"),
        index.get("claims_sha256"),
    )
    conflicts = _load_hashed_artifact(
        root,
        index.get("conflicts_path"),
        index.get("conflicts_sha256"),
    )
    if (
        claims.get("contract_version") != CLAIMS_VERSION
        or claims.get("source_sha256") != source_sha256
        or not isinstance(claims.get("claims"), list)
        or conflicts.get("contract_version") != CONFLICTS_VERSION
        or conflicts.get("source_sha256") != source_sha256
        or not isinstance(conflicts.get("conflicts"), list)
    ):
        raise ContractValidationError("source evidence claims/conflicts 非法")
    claim_ids: set[str] = set()
    for claim in claims["claims"]:
        if not isinstance(claim, dict):
            raise ContractValidationError("source evidence claim 非法")
        claim_id = claim.get("claim_id")
        evidence_ids = claim.get("evidence_observation_ids")
        if (
            not isinstance(claim_id, str)
            or not claim_id
            or claim_id in claim_ids
            or claim.get("provider_id") not in provider_ids
            or not isinstance(claim.get("capability"), str)
            or not isinstance(claim.get("claim_type"), str)
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(isinstance(item, str) for item in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
            or not set(evidence_ids).issubset(observation_by_id)
            or not isinstance(claim.get("payload"), dict)
            or claim.get("status") not in {"proposed", "accepted", "rejected"}
        ):
            raise ContractValidationError("source evidence claim 字段或引用非法")
        claim_ids.add(claim_id)
    conflict_ids: set[str] = set()
    for conflict in conflicts["conflicts"]:
        if not isinstance(conflict, dict):
            raise ContractValidationError("source evidence conflict 非法")
        conflict_id = conflict.get("conflict_id")
        observation_ids = conflict.get("observation_ids")
        related_claim_ids = conflict.get("claim_ids")
        if (
            not isinstance(conflict_id, str)
            or not conflict_id
            or conflict_id in conflict_ids
            or not isinstance(conflict.get("kind"), str)
            or not isinstance(observation_ids, list)
            or not isinstance(related_claim_ids, list)
            or not all(isinstance(item, str) for item in observation_ids)
            or not all(isinstance(item, str) for item in related_claim_ids)
            or not set(observation_ids).issubset(observation_by_id)
            or not set(related_claim_ids).issubset(claim_ids)
            or conflict.get("status") not in {"open", "resolved", "degraded"}
        ):
            raise ContractValidationError("source evidence conflict 字段或引用非法")
        conflict_ids.add(conflict_id)
    summary = index.get("summary")
    expected_summary = {
        "provider_count": len(providers),
        "observation_count": len(observation_by_id),
        "alignment_count": len(alignments_doc["alignments"]),
        "claim_count": len(claims["claims"]),
        "conflict_count": len(conflicts["conflicts"]),
    }
    if summary != expected_summary:
        raise ContractValidationError("source evidence summary 不匹配")
    return index


def write_pdfium_source_evidence(
    root: Path,
    document: PhysicalDocument,
    *,
    source: Path | None = None,
) -> dict[str, Any]:
    """Write PDFium evidence plus the default pdfplumber sidecar when given."""

    root = Path(root)
    if root.exists():
        raise ContractValidationError("source evidence 输出目录已存在，拒绝覆盖")
    index, artifacts = build_pdfium_source_evidence(document)
    if source is not None:
        from .pdfplumber_provider import build_pdfplumber_evidence

        snapshot, sidecar_alignments, sidecar_claims = (
            build_pdfplumber_evidence(Path(source), document)
        )
        snapshot_path = "providers/pdfplumber-geometry.json"
        artifacts[snapshot_path] = snapshot
        artifacts["alignments.json"]["alignments"].extend(sidecar_alignments)
        artifacts["claims.json"]["claims"].extend(sidecar_claims)
        index["providers"].append(
            {
                "provider_id": snapshot["provider_id"],
                "version": snapshot["provider_version"],
                "capabilities": snapshot["capabilities"],
                "missing_capabilities": snapshot["missing_capabilities"],
                "snapshot_path": snapshot_path,
                "snapshot_sha256": _sha256_bytes(
                    _canonical_json(snapshot).encode("utf-8")
                ),
                "status": snapshot["status"],
            }
        )
        index["alignments_sha256"] = _sha256_bytes(
            _canonical_json(artifacts["alignments.json"]).encode("utf-8")
        )
        index["claims_sha256"] = _sha256_bytes(
            _canonical_json(artifacts["claims.json"]).encode("utf-8")
        )
        index["summary"] = {
            "provider_count": len(index["providers"]),
            "observation_count": sum(
                int(value["observation_count"])
                for path, value in artifacts.items()
                if path.startswith("providers/")
            ),
            "alignment_count": len(
                artifacts["alignments.json"]["alignments"]
            ),
            "claim_count": len(artifacts["claims.json"]["claims"]),
            "conflict_count": len(artifacts["conflicts.json"]["conflicts"]),
        }
    (root / "providers").mkdir(parents=True)
    for relative, value in artifacts.items():
        path = root / relative
        path.write_text(_canonical_json(value), encoding="utf-8", newline="\n")
    index_path = root / "index.json"
    index_path.write_text(_canonical_json(index), encoding="utf-8", newline="\n")
    validate_source_evidence_bundle(root)
    return {
        "path": "source-evidence/index.json",
        "sha256": _sha256_file(index_path),
        "contract_version": SOURCE_EVIDENCE_VERSION,
        "provider_count": index["summary"]["provider_count"],
        "observation_count": index["summary"]["observation_count"],
    }
