"""Restricted paper-level decisions and a deterministic structural tree.

The recipe is deliberately data, not executable Python.  It may decide how
native PDF elements are classified, excluded, ordered, bound, or rendered,
but it never carries replacement text or Markdown.  The ArticleTree compiler
then proves that every physical element has exactly one structural leaf.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .exceptions import ContractValidationError
from .layout_models import FinalLayout, LayoutRegion, NormalizedBBox
from .models import BBox, PhysicalDocument
from .source_evidence import validate_source_evidence_bundle


PAPER_RECIPE_VERSION = "paperwright-paper-recipe-v0.1"
ARTICLE_TREE_VERSION = "paperwright-article-tree-v0.1"
RECIPE_RUNTIME_VERSION = "paperwright-recipe-runtime-v0.1"
ARTICLE_TREE_COMPILER_VERSION = "paperwright-article-tree-compiler-v0.1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RASTER_EVIDENCE_RE = re.compile(
    r"^raster-residual:p[0-9]{4}:RV[0-9]{4}:[0-9a-f]{64}$"
)
_CAPTION_RE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?\s*(?:[|.:])",
    re.IGNORECASE,
)
_OPERATIONS = frozenset(
    {"classify", "exclude", "split", "merge", "order", "bind", "render"}
)
_DISPOSITIONS = frozenset({"keep", "exclude", "render"})
_ROLE_MAP = {
    "title": "title",
    "author": "author",
    "affiliation": "affiliation",
    "abstract": "abstract",
    "section_heading": "heading",
    "paragraph": "body",
    "list_item": "body",
    "figure_caption": "caption",
    "table_caption": "caption",
    "caption": "caption",
    "inline_citation": "body",
    "reference": "reference",
    "page_header": "header",
    "page_footer": "footer",
    "display_equation": "equation",
}


def _canonical_json(value: Mapping[str, Any]) -> str:
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


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{prefix}-{_sha256_bytes(payload.encode('utf-8'))[:20]}"


def _intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.y, right.y))
    return width * height


def _normalized_bbox(box: BBox, document: PhysicalDocument, page_index: int) -> dict[str, float]:
    page = document.pages[page_index]
    return NormalizedBBox.from_pdf_bbox(
        box,
        page_width=page.width,
        page_height=page.height,
    ).to_dict()


def _bbox_elements(
    document: PhysicalDocument,
    page_index: int,
    bbox: BBox,
    *,
    kinds: frozenset[str] | None = None,
) -> list[str]:
    result: list[str] = []
    for element in document.pages[page_index].elements:
        if kinds is not None and element.kind not in kinds:
            continue
        area = element.bbox.width * element.bbox.height
        if _intersection_area(element.bbox, bbox) / max(area, 1e-9) >= 0.5:
            result.append(element.element_id)
    return sorted(result)


def _load_artifact(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractValidationError("PaperRecipe evidence artifact 顶层必须是对象")
    return value


def _action(
    operation: str,
    *,
    page_index: int,
    element_ids: Sequence[str],
    role: str | None,
    disposition: str,
    bbox: Mapping[str, float] | None,
    evidence_refs: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    identity = {
        "operation": operation,
        "page_index": page_index,
        "element_ids": sorted(element_ids),
        "role": role,
        "disposition": disposition,
        "bbox": dict(bbox) if bbox is not None else None,
        "evidence_refs": sorted(evidence_refs),
        "reason": reason,
    }
    return {
        "action_id": _stable_id("recipe", identity),
        **identity,
    }


def build_paper_recipe(
    document: PhysicalDocument,
    evidence_root: Path,
    *,
    raster_analyses: Mapping[int, object] | None = None,
) -> dict[str, Any]:
    """Plan conservative paper-level operations from immutable evidence."""

    evidence_root = Path(evidence_root)
    index = validate_source_evidence_bundle(evidence_root)
    if index["source_sha256"] != document.source_sha256:
        raise ContractValidationError("PaperRecipe 与 SourceEvidence source hash 不一致")
    claims_doc = _load_artifact(evidence_root, str(index["claims_path"]))
    conflicts_doc = _load_artifact(evidence_root, str(index["conflicts_path"]))
    alignments_doc = _load_artifact(evidence_root, str(index["alignments_path"]))
    physical_by_observation = {
        item["observation_id"]: item["physical_element_id"]
        for item in alignments_doc["alignments"]
    }
    actions: list[dict[str, Any]] = []

    # A raster-only publisher mark has no PhysicalDocument element to target.
    # Keep this exception page-1-only and geometrically narrow.  The action is
    # still bound to the deterministic residual-mask hash and ROI, and cannot
    # delete or replace native text.
    first_analysis = (raster_analyses or {}).get(0)
    for region in getattr(first_analysis, "regions", ()):
        box = region.bbox
        if not (
            0.45 <= box.y <= 0.68
            and box.width <= 0.25
            and box.height <= 0.10
            and region.page_area_ratio <= 0.012
            and region.residual_coverage >= 0.08
        ):
            continue
        actions.append(
            _action(
                "exclude",
                page_index=0,
                element_ids=[],
                role="furniture",
                disposition="exclude",
                bbox=box.to_dict(),
                evidence_refs=[
                    f"raster-residual:p0000:{region.region_id}:"
                    f"{first_analysis.residual_mask_sha256}"
                ],
                reason="small_uncaptioned_first_page_raster_furniture",
            )
        )

    # Explicit native caption markers are the only unconditional caption
    # authority.  This prevents arbitrary first-page sidebars from becoming a
    # caption merely because they are near a logo.
    for page in document.pages:
        for element in page.elements:
            if element.kind == "text" and element.text and _CAPTION_RE.match(element.text):
                actions.append(
                    _action(
                        "classify",
                        page_index=page.page_index,
                        element_ids=[element.element_id],
                        role="caption",
                        disposition="keep",
                        bbox=_normalized_bbox(element.bbox, document, page.page_index),
                        evidence_refs=[f"pdfium-native:{element.element_id}"],
                        reason="native_explicit_figure_or_table_caption",
                    )
                )

    table_claim_ids: set[str] = set()
    for claim in sorted(claims_doc["claims"], key=lambda item: item["claim_id"]):
        if claim.get("status") == "rejected":
            continue
        claim_type = str(claim["claim_type"])
        payload = claim["payload"]
        evidence_ids = list(claim["evidence_observation_ids"])
        physical_ids = sorted(
            {
                physical_by_observation[item]
                for item in evidence_ids
                if item in physical_by_observation
            }
        )
        if claim_type in {"table_region", "table"}:
            raw_bbox = payload.get("paperwright_bbox")
            page_index = payload.get("page_index")
            if page_index is None:
                page_indices = payload.get("page_indices", [])
                page_index = page_indices[0] if len(page_indices) == 1 else None
            if isinstance(page_index, int) and isinstance(raw_bbox, dict):
                box = BBox.from_dict(raw_bbox)
                physical_ids = _bbox_elements(
                    document,
                    page_index,
                    box,
                    kinds=frozenset({"text", "image", "vector"}),
                )
                if physical_ids:
                    actions.append(
                        _action(
                            "render",
                            page_index=page_index,
                            element_ids=physical_ids,
                            role="table",
                            disposition="render",
                            bbox=_normalized_bbox(box, document, page_index),
                            evidence_refs=[claim["claim_id"], *evidence_ids],
                            reason="provider_table_boundary_preserved_as_image",
                        )
                    )
                    table_claim_ids.add(str(claim["claim_id"]))
            continue
        mapped_role = _ROLE_MAP.get(claim_type)
        if mapped_role is None or not physical_ids:
            continue
        page_by_id = {
            item.element_id: item.page_index
            for page in document.pages
            for item in page.elements
        }
        by_page: dict[int, list[str]] = {}
        for element_id in physical_ids:
            by_page.setdefault(page_by_id[element_id], []).append(element_id)
        for page_index, element_ids in sorted(by_page.items()):
            actions.append(
                _action(
                    "classify",
                    page_index=page_index,
                    element_ids=element_ids,
                    role=mapped_role,
                    disposition=(
                        "exclude" if mapped_role in {"header", "footer"} else "keep"
                    ),
                    bbox=None,
                    evidence_refs=[claim["claim_id"], *evidence_ids],
                    reason="provider_scholarly_role_claim",
                )
            )

    # Preserve native images as visuals.  A small first-page image without an
    # explicit native caption is treated as publication furniture/logo; this
    # is narrow by design and does not affect scientific figures on later pages.
    explicit_caption_pages = {
        action["page_index"]
        for action in actions
        if action["role"] == "caption"
    }
    table_elements = {
        element_id
        for action in actions
        if action["operation"] == "render" and action["role"] == "table"
        for element_id in action["element_ids"]
    }
    first_page = document.pages[0]
    bottom_furniture_vectors = sorted(
        (
            item
            for item in first_page.elements
            if item.kind == "vector"
            and item.bbox.y / first_page.height >= 0.82
            and (
                item.bbox.width
                * item.bbox.height
                / (first_page.width * first_page.height)
            )
            <= 0.05
        ),
        key=lambda item: item.element_id,
    )
    if bottom_furniture_vectors:
        left = min(item.bbox.x for item in bottom_furniture_vectors)
        top = min(item.bbox.y for item in bottom_furniture_vectors)
        right = max(item.bbox.right for item in bottom_furniture_vectors)
        bottom = max(item.bbox.bottom for item in bottom_furniture_vectors)
        actions.append(
            _action(
                "exclude",
                page_index=0,
                element_ids=[item.element_id for item in bottom_furniture_vectors],
                role="furniture",
                disposition="exclude",
                bbox=_normalized_bbox(
                    BBox(left, top, right - left, bottom - top),
                    document,
                    0,
                ),
                evidence_refs=[
                    f"pdfium-native:{item.element_id}"
                    for item in bottom_furniture_vectors
                ],
                reason="first_page_bottom_publication_vector_furniture",
            )
        )
    for page in document.pages:
        for element in page.elements:
            if element.kind != "image" or element.element_id in table_elements:
                continue
            area_ratio = (
                element.bbox.width
                * element.bbox.height
                / (page.width * page.height)
            )
            if (
                page.page_index == 0
                and area_ratio <= 0.02
                and page.page_index not in explicit_caption_pages
            ):
                actions.append(
                    _action(
                        "exclude",
                        page_index=page.page_index,
                        element_ids=[element.element_id],
                        role="furniture",
                        disposition="exclude",
                        bbox=_normalized_bbox(element.bbox, document, page.page_index),
                        evidence_refs=[f"pdfium-native:{element.element_id}"],
                        reason="small_uncaptioned_first_page_native_image",
                    )
                )
            else:
                actions.append(
                    _action(
                        "render",
                        page_index=page.page_index,
                        element_ids=[element.element_id],
                        role="figure",
                        disposition="render",
                        bbox=_normalized_bbox(element.bbox, document, page.page_index),
                        evidence_refs=[f"pdfium-native:{element.element_id}"],
                        reason="native_image_object_preservation",
                    )
                )

    actions.sort(
        key=lambda item: (
            item["page_index"],
            {"exclude": 0, "render": 1, "classify": 2}.get(item["operation"], 9),
            item["action_id"],
        )
    )
    open_conflicts = [
        str(item["conflict_id"])
        for item in conflicts_doc["conflicts"]
        if item["status"] in {"open", "degraded"}
    ]
    unhandled_conflicts = [
        str(item["conflict_id"])
        for item in conflicts_doc["conflicts"]
        if item["status"] in {"open", "degraded"}
        and not set(item.get("claim_ids", ())) & table_claim_ids
    ]
    trace_sha256 = _sha256_bytes(_canonical_json({"actions": actions}).encode("utf-8"))
    value = {
        "contract_version": PAPER_RECIPE_VERSION,
        "runtime_version": RECIPE_RUNTIME_VERSION,
        "source_sha256": document.source_sha256,
        "physical_document_sha256": document.deterministic_sha256(),
        "source_evidence_index_sha256": _sha256_file(evidence_root / "index.json"),
        "producer": "paperwright-deterministic-baseline",
        "allowed_operations": sorted(_OPERATIONS),
        "forbidden_capabilities": [
            "filesystem",
            "network",
            "randomness",
            "replace_body_text",
            "write_markdown",
        ],
        "actions": actions,
        "unresolved_conflict_ids": sorted(open_conflicts),
        "status": (
            "human_required"
            if unhandled_conflicts
            else "degraded"
            if open_conflicts or index["status"] != "complete"
            else "ready"
        ),
        "replay": {
            "deterministic": True,
            "trace_sha256": trace_sha256,
            "same_input_same_actions": True,
        },
    }
    validate_paper_recipe(value, document=document, evidence_root=evidence_root)
    return value


def validate_paper_recipe(
    value: Mapping[str, Any],
    *,
    document: PhysicalDocument,
    evidence_root: Path,
) -> None:
    """Validate authority limits, evidence bindings and native IDs."""

    required = {
        "contract_version",
        "runtime_version",
        "source_sha256",
        "physical_document_sha256",
        "source_evidence_index_sha256",
        "producer",
        "allowed_operations",
        "forbidden_capabilities",
        "actions",
        "unresolved_conflict_ids",
        "status",
        "replay",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ContractValidationError("PaperRecipe 顶层字段非法")
    if (
        value["contract_version"] != PAPER_RECIPE_VERSION
        or value["runtime_version"] != RECIPE_RUNTIME_VERSION
        or value["source_sha256"] != document.source_sha256
        or value["physical_document_sha256"] != document.deterministic_sha256()
        or value["source_evidence_index_sha256"]
        != _sha256_file(Path(evidence_root) / "index.json")
    ):
        raise ContractValidationError("PaperRecipe 输入身份或版本不匹配")
    if value["allowed_operations"] != sorted(_OPERATIONS):
        raise ContractValidationError("PaperRecipe allowed_operations 非法")
    if value["forbidden_capabilities"] != [
        "filesystem",
        "network",
        "randomness",
        "replace_body_text",
        "write_markdown",
    ]:
        raise ContractValidationError("PaperRecipe forbidden_capabilities 非法")
    if value["status"] not in {"ready", "degraded", "human_required"}:
        raise ContractValidationError("PaperRecipe status 非法")
    evidence_index = validate_source_evidence_bundle(Path(evidence_root))
    valid_evidence_refs: set[str] = set()
    for provider in evidence_index["providers"]:
        snapshot = _load_artifact(
            Path(evidence_root),
            str(provider["snapshot_path"]),
        )
        valid_evidence_refs.update(
            observation["observation_id"]
            for page in snapshot.get("pages", ())
            for observation in page.get("observations", ())
        )
    claims_doc = _load_artifact(
        Path(evidence_root),
        str(evidence_index["claims_path"]),
    )
    conflicts_doc = _load_artifact(
        Path(evidence_root),
        str(evidence_index["conflicts_path"]),
    )
    valid_evidence_refs.update(
        item["claim_id"] for item in claims_doc["claims"]
    )
    conflict_ids = {
        item["conflict_id"] for item in conflicts_doc["conflicts"]
    }
    valid_evidence_refs.update(conflict_ids)
    unresolved = value["unresolved_conflict_ids"]
    if (
        not isinstance(unresolved, list)
        or unresolved != sorted(set(unresolved))
        or not set(unresolved).issubset(conflict_ids)
        or value["status"] == "ready"
        and unresolved
    ):
        raise ContractValidationError("PaperRecipe unresolved conflicts 非法")
    element_by_id = {
        item.element_id: item
        for page in document.pages
        for item in page.elements
    }
    action_ids: set[str] = set()
    for action in value["actions"]:
        expected_fields = {
            "action_id",
            "operation",
            "page_index",
            "element_ids",
            "role",
            "disposition",
            "bbox",
            "evidence_refs",
            "reason",
        }
        if not isinstance(action, Mapping) or set(action) != expected_fields:
            raise ContractValidationError("PaperRecipe action 字段非法")
        action_id = action["action_id"]
        element_ids = action["element_ids"]
        if (
            not isinstance(action_id, str)
            or action_id in action_ids
            or action["operation"] not in _OPERATIONS
            or action["disposition"] not in _DISPOSITIONS
            or not isinstance(action["page_index"], int)
            or not isinstance(element_ids, list)
            or (
                not element_ids
                and not (
                    action["operation"] == "exclude"
                    and action["bbox"] is not None
                )
            )
            or element_ids != sorted(set(element_ids))
            or not set(element_ids).issubset(element_by_id)
            or any(element_by_id[item].page_index != action["page_index"] for item in element_ids)
            or not isinstance(action["evidence_refs"], list)
            or not action["evidence_refs"]
            or action["evidence_refs"]
            != sorted(set(action["evidence_refs"]))
            or any(
                item not in valid_evidence_refs
                and _RASTER_EVIDENCE_RE.fullmatch(item) is None
                for item in action["evidence_refs"]
            )
            or not isinstance(action["reason"], str)
            or not action["reason"]
            or action["role"] is not None
            and (
                not isinstance(action["role"], str)
                or not action["role"]
            )
            or action["operation"] == "render"
            and (
                action["disposition"] != "render"
                or action["bbox"] is None
            )
            or action["operation"] == "exclude"
            and action["disposition"] != "exclude"
        ):
            raise ContractValidationError("PaperRecipe action 引用或取值非法")
        if action["bbox"] is not None:
            NormalizedBBox.from_dict(action["bbox"])
        identity = {key: action[key] for key in expected_fields - {"action_id"}}
        if action_id != _stable_id("recipe", identity):
            raise ContractValidationError("PaperRecipe action_id 与内容不匹配")
        action_ids.add(action_id)
    replay = value["replay"]
    expected_trace = _sha256_bytes(
        _canonical_json({"actions": list(value["actions"])}).encode("utf-8")
    )
    if (
        not isinstance(replay, Mapping)
        or replay.get("deterministic") is not True
        or replay.get("same_input_same_actions") is not True
        or replay.get("trace_sha256") != expected_trace
    ):
        raise ContractValidationError("PaperRecipe replay trace 不匹配")


def canonical_paper_recipe_json(value: Mapping[str, Any]) -> str:
    return _canonical_json(value)


def paper_recipe_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_paper_recipe_json(value).encode("utf-8"))


def compile_article_tree(
    document: PhysicalDocument,
    recipe: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile one lossless leaf per physical element with recipe decisions."""

    action_by_element: dict[str, list[Mapping[str, Any]]] = {}
    for action in recipe["actions"]:
        for element_id in action["element_ids"]:
            action_by_element.setdefault(element_id, []).append(action)
    nodes: list[dict[str, Any]] = [
        {
            "node_id": "document",
            "parent_id": None,
            "kind": "document",
            "role": "paper",
            "order": 0,
            "page_index": None,
            "source_element_ids": [],
            "source_text_sha256": None,
            "disposition": "keep",
            "decision_action_ids": [],
        }
    ]
    leaf_order = 0
    for page in document.pages:
        page_id = f"page-{page.page_index:04d}"
        nodes.append(
            {
                "node_id": page_id,
                "parent_id": "document",
                "kind": "page",
                "role": "page",
                "order": page.page_index + 1,
                "page_index": page.page_index,
                "source_element_ids": [],
                "source_text_sha256": None,
                "disposition": "keep",
                "decision_action_ids": [],
            }
        )
        for element in sorted(
            page.elements,
            key=lambda item: (
                item.metadata.get("normalized_order", 1_000_000),
                item.bbox.y,
                item.bbox.x,
                item.element_id,
            ),
        ):
            leaf_order += 1
            decisions = action_by_element.get(element.element_id, [])
            dispositions = {item["disposition"] for item in decisions}
            disposition = (
                "exclude"
                if "exclude" in dispositions
                else "render"
                if "render" in dispositions
                else "keep"
            )
            roles = [
                str(item["role"])
                for item in decisions
                if item.get("role") is not None
                and (
                    item["disposition"] == disposition
                    or disposition == "keep"
                )
            ]
            role = roles[-1] if roles else element.kind
            nodes.append(
                {
                    "node_id": f"element:{element.element_id}",
                    "parent_id": page_id,
                    "kind": "source_element",
                    "role": role,
                    "order": leaf_order,
                    "page_index": page.page_index,
                    "source_element_ids": [element.element_id],
                    "source_text_sha256": (
                        _sha256_bytes(element.text.encode("utf-8"))
                        if element.text is not None
                        else None
                    ),
                    "disposition": disposition,
                    "decision_action_ids": sorted(
                        item["action_id"] for item in decisions
                    ),
                }
            )
    value = {
        "contract_version": ARTICLE_TREE_VERSION,
        "compiler_version": ARTICLE_TREE_COMPILER_VERSION,
        "source_sha256": document.source_sha256,
        "physical_document_sha256": document.deterministic_sha256(),
        "paper_recipe_sha256": paper_recipe_sha256(recipe),
        "status": recipe["status"],
        "root_node_id": "document",
        "nodes": nodes,
        "summary": {
            "page_count": len(document.pages),
            "source_element_count": leaf_order,
            "kept_element_count": sum(
                item["kind"] == "source_element" and item["disposition"] == "keep"
                for item in nodes
            ),
            "rendered_element_count": sum(
                item["kind"] == "source_element" and item["disposition"] == "render"
                for item in nodes
            ),
            "excluded_element_count": sum(
                item["kind"] == "source_element" and item["disposition"] == "exclude"
                for item in nodes
            ),
            "generated_text_count": 0,
        },
    }
    validate_article_tree(value, document=document, recipe=recipe)
    return value


def validate_article_tree(
    value: Mapping[str, Any],
    *,
    document: PhysicalDocument,
    recipe: Mapping[str, Any],
) -> None:
    """Validate tree identity, acyclicity, conservation and text authority."""

    required = {
        "contract_version",
        "compiler_version",
        "source_sha256",
        "physical_document_sha256",
        "paper_recipe_sha256",
        "status",
        "root_node_id",
        "nodes",
        "summary",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ContractValidationError("ArticleTree 顶层字段非法")
    if (
        value["contract_version"] != ARTICLE_TREE_VERSION
        or value["compiler_version"] != ARTICLE_TREE_COMPILER_VERSION
        or value["source_sha256"] != document.source_sha256
        or value["physical_document_sha256"] != document.deterministic_sha256()
        or value["paper_recipe_sha256"] != paper_recipe_sha256(recipe)
        or value["root_node_id"] != "document"
        or value["status"] != recipe["status"]
    ):
        raise ContractValidationError("ArticleTree 输入身份或版本不匹配")
    nodes = value["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise ContractValidationError("ArticleTree nodes 不能为空")
    node_ids: set[str] = set()
    leaf_ids: list[str] = []
    known_actions = {item["action_id"] for item in recipe["actions"]}
    expected_node_fields = {
        "node_id",
        "parent_id",
        "kind",
        "role",
        "order",
        "page_index",
        "source_element_ids",
        "source_text_sha256",
        "disposition",
        "decision_action_ids",
    }
    element_by_id = {
        item.element_id: item
        for page in document.pages
        for item in page.elements
    }
    for node in nodes:
        if not isinstance(node, Mapping) or set(node) != expected_node_fields:
            raise ContractValidationError("ArticleTree node 字段非法")
        node_id = node["node_id"]
        if not isinstance(node_id, str) or not node_id or node_id in node_ids:
            raise ContractValidationError("ArticleTree node_id 非法或重复")
        node_ids.add(node_id)
        source_ids = node["source_element_ids"]
        if node["kind"] == "source_element":
            if not isinstance(source_ids, list) or len(source_ids) != 1:
                raise ContractValidationError("ArticleTree source leaf 必须引用一个元素")
            leaf_ids.extend(source_ids)
            element = element_by_id.get(source_ids[0])
            expected_text_sha256 = (
                _sha256_bytes(element.text.encode("utf-8"))
                if element is not None and element.text is not None
                else None
            )
            if (
                element is None
                or node["page_index"] != element.page_index
                or node["parent_id"] != f"page-{element.page_index:04d}"
                or node["source_text_sha256"] != expected_text_sha256
            ):
                raise ContractValidationError("ArticleTree source leaf provenance 非法")
        elif source_ids != [] or node["source_text_sha256"] is not None:
            raise ContractValidationError("ArticleTree 容器节点不得携带正文")
        if not isinstance(node["role"], str) or not node["role"]:
            raise ContractValidationError("ArticleTree role 非法")
        if (
            node["disposition"] not in _DISPOSITIONS
            or not isinstance(node["decision_action_ids"], list)
            or not set(node["decision_action_ids"]).issubset(known_actions)
        ):
            raise ContractValidationError("ArticleTree decision 非法")
        text_hash = node["source_text_sha256"]
        if text_hash is not None and (
            not isinstance(text_hash, str) or _HASH_RE.fullmatch(text_hash) is None
        ):
            raise ContractValidationError("ArticleTree source_text_sha256 非法")
    if any(
        node["parent_id"] is not None and node["parent_id"] not in node_ids
        for node in nodes
    ):
        raise ContractValidationError("ArticleTree parent_id 引用未知节点")
    expected_ids = [
        item.element_id for page in document.pages for item in page.elements
    ]
    if len(leaf_ids) != len(set(leaf_ids)) or set(leaf_ids) != set(expected_ids):
        raise ContractValidationError("ArticleTree 未守恒覆盖 PhysicalDocument 元素")
    summary = value["summary"]
    if not isinstance(summary, Mapping) or set(summary) != {
        "page_count",
        "source_element_count",
        "kept_element_count",
        "rendered_element_count",
        "excluded_element_count",
        "generated_text_count",
    }:
        raise ContractValidationError("ArticleTree summary 字段非法")
    if (
        summary.get("page_count") != len(document.pages)
        or summary.get("source_element_count") != len(expected_ids)
        or summary.get("generated_text_count") != 0
        or sum(
            summary.get(name, -1)
            for name in (
                "kept_element_count",
                "rendered_element_count",
                "excluded_element_count",
            )
        )
        != len(expected_ids)
    ):
        raise ContractValidationError("ArticleTree summary 不守恒")


def canonical_article_tree_json(value: Mapping[str, Any]) -> str:
    return _canonical_json(value)


def _region_contains_action(region: LayoutRegion, action: Mapping[str, Any]) -> bool:
    target_ids = set(action["element_ids"])
    if target_ids & set(region.source_element_ids):
        return True
    raw_bbox = action.get("bbox")
    if not isinstance(raw_bbox, Mapping):
        return False
    action_bbox = NormalizedBBox.from_dict(raw_bbox)
    overlap = max(0.0, min(region.bbox.right, action_bbox.right) - max(region.bbox.x, action_bbox.x)) * max(
        0.0, min(region.bbox.bottom, action_bbox.bottom) - max(region.bbox.y, action_bbox.y)
    )
    return overlap / max(action_bbox.width * action_bbox.height, 1e-9) >= 0.5


def refine_layouts_with_recipe(
    document: PhysicalDocument,
    layouts: Sequence[FinalLayout],
    recipe: Mapping[str, Any],
    *,
    protected_caption_keys: frozenset[tuple[int, str]] = frozenset(),
    protected_visual_keys: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[FinalLayout, ...]:
    """Project safe recipe operations onto already materialized layouts."""

    actions_by_page: dict[int, list[Mapping[str, Any]]] = {}
    for action in recipe["actions"]:
        actions_by_page.setdefault(int(action["page_index"]), []).append(action)
    output: list[FinalLayout] = []
    for page, layout in zip(document.pages, layouts, strict=True):
        page_actions = actions_by_page.get(page.page_index, [])
        render_actions = [
            item for item in page_actions if item["operation"] == "render"
        ]
        bbox_exclude_actions = [
            item
            for item in page_actions
            if item["operation"] == "exclude" and item.get("bbox") is not None
        ]
        excluded_ids = {
            element_id
            for item in page_actions
            if item["disposition"] == "exclude"
            for element_id in item["element_ids"]
        }
        caption_ids = {
            element_id
            for item in page_actions
            if item["role"] == "caption" and item["disposition"] == "keep"
            for element_id in item["element_ids"]
        }
        rendered_ids = {
            element_id
            for item in render_actions
            for element_id in item["element_ids"]
        }
        regions: list[LayoutRegion] = []
        used_render_actions: set[str] = set()
        for region in layout.regions:
            source_ids = tuple(
                item
                for item in region.source_element_ids
                if item not in excluded_ids and item not in rendered_ids
            )
            matching_render = next(
                (
                    item
                    for item in render_actions
                    if _region_contains_action(region, item)
                ),
                None,
            )
            matching_bbox_exclude = next(
                (
                    item
                    for item in bbox_exclude_actions
                    if _region_contains_action(region, item)
                ),
                None,
            )
            if matching_render is not None and region.content_class == "visual":
                used_render_actions.add(str(matching_render["action_id"]))
                source_ids = tuple(matching_render["element_ids"])
                regions.append(
                    LayoutRegion(
                        region_id=region.region_id,
                        bbox=NormalizedBBox.from_dict(matching_render["bbox"]),
                        content_class="visual",
                        role=str(matching_render["role"]),
                        order=region.order,
                        source_candidate_ids=region.source_candidate_ids,
                        source_element_ids=source_ids,
                        parent_region_id=region.parent_region_id,
                        confidence=region.confidence,
                    )
                )
                continue
            excluded_visual_objects = {
                item
                for item in region.source_element_ids
                if item in excluded_ids
                and any(
                    element.element_id == item
                    and element.kind in {"image", "vector"}
                    for element in page.elements
                )
            }
            region_visual_objects = {
                item.element_id
                for item in page.elements
                if item.element_id in region.source_element_ids
                and item.kind in {"image", "vector"}
            }
            should_exclude_visual = (
                matching_bbox_exclude is not None
                and matching_render is None
            ) or (
                bool(region.source_element_ids) and not source_ids
            ) or (
                bool(region_visual_objects)
                and region_visual_objects == excluded_visual_objects
            )
            if region.content_class == "visual" and (
                (page.page_index, region.region_id)
                not in protected_visual_keys
                and should_exclude_visual
            ):
                regions.append(
                    LayoutRegion(
                        region_id=region.region_id,
                        bbox=region.bbox,
                        content_class="exclude",
                        role="other",
                        order=None,
                        source_candidate_ids=region.source_candidate_ids,
                        source_element_ids=tuple(sorted(excluded_ids & set(region.source_element_ids))),
                        parent_region_id=region.parent_region_id,
                        confidence=region.confidence,
                    )
                )
                continue
            role = region.role
            if (
                role == "caption"
                and (page.page_index, region.region_id)
                not in protected_caption_keys
                and not caption_ids.intersection(source_ids)
            ):
                role = "margin" if page.page_index == 0 else "body"
            regions.append(
                LayoutRegion(
                    region_id=region.region_id,
                    bbox=region.bbox,
                    content_class=region.content_class,
                    role=role,
                    order=region.order,
                    source_candidate_ids=region.source_candidate_ids,
                    source_element_ids=source_ids,
                    parent_region_id=region.parent_region_id,
                    confidence=region.confidence,
                )
            )
        for action in render_actions:
            if action["action_id"] in used_render_actions:
                continue
            action_bbox = NormalizedBBox.from_dict(action["bbox"])
            order_hint = 1 + sum(
                item.content_class != "exclude"
                and (item.order or 0) > 0
                and item.bbox.y < action_bbox.y
                for item in regions
            )
            regions.append(
                LayoutRegion(
                    region_id=f"recipe-{action['action_id']}",
                    bbox=action_bbox,
                    content_class="visual",
                    role=str(action["role"]),
                    order=order_hint,
                    source_candidate_ids=(),
                    source_element_ids=tuple(action["element_ids"]),
                    parent_region_id=None,
                    confidence=1.0,
                )
            )
        non_excluded = [item for item in regions if item.content_class != "exclude"]
        non_excluded.sort(
            key=lambda item: (
                item.order if item.order is not None else 1_000_000,
                item.bbox.y,
                item.bbox.x,
                item.region_id,
            )
        )
        order_by_id = {
            item.region_id: index
            for index, item in enumerate(non_excluded, start=1)
        }
        normalized_regions = tuple(
            LayoutRegion(
                region_id=item.region_id,
                bbox=item.bbox,
                content_class=item.content_class,
                role=item.role,
                order=(
                    order_by_id[item.region_id]
                    if item.content_class != "exclude"
                    else None
                ),
                source_candidate_ids=item.source_candidate_ids,
                source_element_ids=item.source_element_ids,
                parent_region_id=item.parent_region_id,
                confidence=item.confidence,
            )
            for item in regions
        )
        output.append(
            FinalLayout(
                source_sha256=layout.source_sha256,
                page=layout.page,
                regions=normalized_regions,
                actions=layout.actions,
                reviewer=f"{layout.reviewer}+paper-recipe-v0.1",
                prompt_version=layout.prompt_version,
                warnings=layout.warnings,
            )
        )
    return tuple(output)


__all__ = [
    "ARTICLE_TREE_VERSION",
    "PAPER_RECIPE_VERSION",
    "build_paper_recipe",
    "canonical_article_tree_json",
    "canonical_paper_recipe_json",
    "compile_article_tree",
    "paper_recipe_sha256",
    "refine_layouts_with_recipe",
    "validate_article_tree",
    "validate_paper_recipe",
]
