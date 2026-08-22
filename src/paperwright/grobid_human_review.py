"""Offline, blind human-review contracts and renderer for GROBID claims."""

from __future__ import annotations

import hashlib
import html
import json
import math
from copy import deepcopy
from typing import Any, Mapping

from .exceptions import ContractValidationError
from .grobid_evaluation import GROBID_AUDIT_TASK_VERSION

GROBID_HUMAN_REVIEW_LEGACY_VERSION = "paperwright-grobid-human-review-v0.1"
GROBID_HUMAN_REVIEW_VERSION = "paperwright-grobid-human-review-v0.2"
GROBID_HUMAN_REVIEW_MANIFEST_VERSION = (
    "paperwright-grobid-human-review-manifest-v0.2"
)
REVIEW_LABELS = (
    "correct",
    "partial",
    "wrong_role",
    "unsupported",
    "uncertain",
)
RECALL_GOLD_TYPES = (
    "title",
    "abstract",
    "section_heading",
    "figure_caption",
    "table_caption",
    "reference",
)
GOLD_STATUSES = ("in_progress", "complete", "not_applicable")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def grobid_audit_task_sha256(task: Mapping[str, Any]) -> str:
    """Return the canonical task hash bound into every review response."""

    return _sha256_text(_canonical_json(task))


def _fail(message: str) -> None:
    raise ContractValidationError(message)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def validate_grobid_audit_task(task: Mapping[str, Any]) -> None:
    """Validate the blind v0.2 task needed by the offline reviewer."""

    if task.get("contract_version") != GROBID_AUDIT_TASK_VERSION:
        _fail("GROBID audit task contract_version 不支持")
    document_id = task.get("document_id")
    source_sha256 = task.get("source_sha256")
    claims = task.get("claims")
    pages = task.get("page_images")
    if (
        not isinstance(document_id, str)
        or not document_id
        or not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or not isinstance(claims, list)
        or task.get("claim_count") != len(claims)
        or not isinstance(pages, list)
        or task.get("review_labels") != list(REVIEW_LABELS)
        or task.get("downstream_adoption_disclosed") is not False
    ):
        _fail("GROBID audit task 顶层字段非法")
    page_geometry: dict[int, tuple[float, float]] = {}
    for page in pages:
        if not isinstance(page, dict):
            _fail("GROBID audit task page image 非法")
        page_index = page.get("page_index")
        width = page.get("width")
        height = page.get("height")
        if (
            not isinstance(page_index, int)
            or page_index < 0
            or page_index in page_geometry
            or not _finite(width)
            or not _finite(height)
            or float(width) <= 0
            or float(height) <= 0
            or not isinstance(page.get("path"), str)
            or not page["path"]
            or not isinstance(page.get("sha256"), str)
            or len(page["sha256"]) != 64
        ):
            _fail("GROBID audit task page image字段非法")
        page_geometry[page_index] = (float(width), float(height))
    claim_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            _fail("GROBID audit claim 非法")
        claim_id = claim.get("claim_id")
        segments = claim.get("segments")
        if (
            not isinstance(claim_id, str)
            or not claim_id
            or claim_id in claim_ids
            or not isinstance(claim.get("claim_type"), str)
            or not claim["claim_type"]
            or not isinstance(segments, list)
            or not segments
        ):
            _fail("GROBID audit claim 字段非法")
        claim_ids.add(claim_id)
        for segment in segments:
            if not isinstance(segment, dict):
                _fail("GROBID audit segment 非法")
            page_index = segment.get("page_index")
            bbox = segment.get("paperwright_bbox")
            if (
                page_index not in page_geometry
                or not isinstance(segment.get("observation_id"), str)
                or not isinstance(segment.get("text"), str)
                or not isinstance(bbox, dict)
                or not isinstance(segment.get("alignments"), list)
            ):
                _fail("GROBID audit segment 字段非法")
            width, height = page_geometry[page_index]
            x = bbox.get("x")
            y = bbox.get("y")
            box_width = bbox.get("width")
            box_height = bbox.get("height")
            if (
                not all(_finite(item) for item in (x, y, box_width, box_height))
                or float(box_width) <= 0
                or float(box_height) <= 0
                or float(x) < 0
                or float(y) < 0
                or float(x) + float(box_width) > width + 1e-6
                or float(y) + float(box_height) > height + 1e-6
            ):
                _fail("GROBID audit segment bbox 非法")
            for alignment in segment["alignments"]:
                if (
                    not isinstance(alignment, dict)
                    or not isinstance(alignment.get("physical_element_id"), str)
                    or not isinstance(alignment.get("native_observation_id"), str)
                    or not isinstance(alignment.get("native_text"), str)
                    or not isinstance(alignment.get("native_bbox"), dict)
                    or not _finite(alignment.get("text_score"))
                    or not _finite(alignment.get("geometry_score"))
                ):
                    _fail("GROBID audit alignment 缺少原生证据")


def build_grobid_human_review_template(
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an empty, task-bound response without inventing gold labels."""

    validate_grobid_audit_task(task)
    return {
        "contract_version": GROBID_HUMAN_REVIEW_VERSION,
        "task_sha256": grobid_audit_task_sha256(task),
        "document_id": task["document_id"],
        "source_sha256": task["source_sha256"],
        "reviewer": "",
        "claim_annotations": [
            {"claim_id": claim["claim_id"], "label": None, "note": ""}
            for claim in task["claims"]
        ],
        "gold_enumeration": {
            claim_type: {"status": "in_progress", "units": []}
            for claim_type in RECALL_GOLD_TYPES
        },
        "completion": {
            "claim_count": len(task["claims"]),
            "claims_labeled": 0,
            "gold_types_complete": 0,
            "ready_for_scoring": False,
        },
    }


def _validate_gold_bbox(
    bbox: object,
    *,
    page_width: float,
    page_height: float,
) -> None:
    if bbox is None:
        return
    if not isinstance(bbox, dict):
        _fail("GROBID human review gold bbox 非法")
    values = tuple(bbox.get(key) for key in ("x", "y", "width", "height"))
    if not all(_finite(value) for value in values):
        _fail("GROBID human review gold bbox 必须为有限数")
    x, y, width, height = (float(value) for value in values)
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > page_width + 1e-6
        or y + height > page_height + 1e-6
    ):
        _fail("GROBID human review gold bbox 越界")


def _completion_for_response(
    task: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    annotations = response.get("claim_annotations", [])
    enumeration = response.get("gold_enumeration", {})
    labeled = sum(
        isinstance(item, dict) and item.get("label") is not None
        for item in annotations
    )
    complete_types = sum(
        isinstance(enumeration.get(claim_type), dict)
        and enumeration[claim_type].get("status")
        in {"complete", "not_applicable"}
        for claim_type in RECALL_GOLD_TYPES
    )
    return {
        "claim_count": len(task["claims"]),
        "claims_labeled": labeled,
        "gold_types_complete": complete_types,
        "ready_for_scoring": (
            labeled == len(task["claims"])
            and complete_types == len(RECALL_GOLD_TYPES)
        ),
    }


def migrate_grobid_human_review_v01(
    task: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    """Migrate a v0.1 response without guessing which units should be merged."""

    validate_grobid_audit_task(task)
    if response.get("contract_version") == GROBID_HUMAN_REVIEW_VERSION:
        migrated = deepcopy(response)
        validate_grobid_human_review(task, migrated)
        return migrated
    if response.get("contract_version") != GROBID_HUMAN_REVIEW_LEGACY_VERSION:
        _fail("GROBID human review migration source contract_version 不支持")
    migrated = deepcopy(response)
    migrated["contract_version"] = GROBID_HUMAN_REVIEW_VERSION
    enumeration = migrated.get("gold_enumeration")
    if not isinstance(enumeration, dict):
        _fail("GROBID human review migration gold_enumeration 非法")
    for claim_type in RECALL_GOLD_TYPES:
        record = enumeration.get(claim_type)
        if not isinstance(record, dict) or not isinstance(record.get("units"), list):
            _fail("GROBID human review migration gold type 非法")
        for unit in record["units"]:
            if not isinstance(unit, dict):
                _fail("GROBID human review migration gold unit 非法")
            unit["segments"] = [
                {
                    "page_index": unit.pop("page_index", None),
                    "text": unit.pop("text", None),
                    "paperwright_bbox": unit.pop("paperwright_bbox", None),
                }
            ]
    migrated["completion"] = _completion_for_response(task, migrated)
    validate_grobid_human_review(task, migrated)
    return migrated


def merge_grobid_gold_units(
    task: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    source_unit_id: str,
    target_unit_id: str,
) -> dict[str, Any]:
    """Merge one continued gold unit into another, preserving segment order."""

    validate_grobid_human_review(task, response)
    merged = deepcopy(response)
    source: tuple[dict[str, Any], int, dict[str, Any]] | None = None
    target: dict[str, Any] | None = None
    for claim_type in RECALL_GOLD_TYPES:
        record = merged.get("gold_enumeration", {}).get(claim_type, {})
        for index, unit in enumerate(record.get("units", [])):
            if unit.get("gold_unit_id") == source_unit_id:
                source = (record, index, unit)
            if unit.get("gold_unit_id") == target_unit_id:
                target = unit
    if source is None or target is None or source[2] is target:
        _fail("GROBID human review merge gold unit ID 非法")
    source_record, source_index, source_unit = source
    if source_unit.get("claim_type") != target.get("claim_type"):
        _fail("GROBID human review 只能合并同类型 gold units")
    target["segments"].extend(source_unit["segments"])
    if source_unit.get("note"):
        separator = "\n" if target.get("note") else ""
        target["note"] = f"{target.get('note', '')}{separator}{source_unit['note']}"
    source_record["units"].pop(source_index)
    merged["completion"] = _completion_for_response(task, merged)
    validate_grobid_human_review(task, merged)
    return merged


def validate_grobid_human_review(
    task: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Validate annotations, recall enumeration and computed completion counts."""

    validate_grobid_audit_task(task)
    if (
        response.get("contract_version") != GROBID_HUMAN_REVIEW_VERSION
        or response.get("task_sha256") != grobid_audit_task_sha256(task)
        or response.get("document_id") != task["document_id"]
        or response.get("source_sha256") != task["source_sha256"]
        or not isinstance(response.get("reviewer"), str)
    ):
        _fail("GROBID human review 与 task 绑定不匹配")
    annotations = response.get("claim_annotations")
    expected_ids = [claim["claim_id"] for claim in task["claims"]]
    if not isinstance(annotations, list) or len(annotations) != len(expected_ids):
        _fail("GROBID human review claim_annotations 不守恒")
    for annotation, expected_id in zip(annotations, expected_ids, strict=True):
        if (
            not isinstance(annotation, dict)
            or annotation.get("claim_id") != expected_id
            or annotation.get("label") not in {*REVIEW_LABELS, None}
            or not isinstance(annotation.get("note"), str)
        ):
            _fail("GROBID human review claim annotation 非法或乱序")
    pages = {page["page_index"]: page for page in task["page_images"]}
    enumeration = response.get("gold_enumeration")
    if not isinstance(enumeration, dict) or set(enumeration) != set(
        RECALL_GOLD_TYPES
    ):
        _fail("GROBID human review gold types 不完整")
    unit_ids: set[str] = set()
    for claim_type in RECALL_GOLD_TYPES:
        record = enumeration[claim_type]
        if (
            not isinstance(record, dict)
            or record.get("status") not in GOLD_STATUSES
            or not isinstance(record.get("units"), list)
        ):
            _fail("GROBID human review gold enumeration 非法")
        if record["status"] == "not_applicable" and record["units"]:
            _fail("not_applicable gold type 不得包含 units")
        for unit in record["units"]:
            if not isinstance(unit, dict):
                _fail("GROBID human review gold unit 非法")
            unit_id = unit.get("gold_unit_id")
            if (
                not isinstance(unit_id, str)
                or not unit_id
                or unit_id in unit_ids
                or unit.get("claim_type") != claim_type
                or not isinstance(unit.get("note"), str)
                or not isinstance(unit.get("segments"), list)
                or not unit["segments"]
            ):
                _fail("GROBID human review gold unit 字段非法")
            unit_ids.add(unit_id)
            for segment in unit["segments"]:
                if not isinstance(segment, dict):
                    _fail("GROBID human review gold segment 非法")
                page_index = segment.get("page_index")
                if (
                    page_index not in pages
                    or not isinstance(segment.get("text"), str)
                    or not segment["text"].strip()
                ):
                    _fail("GROBID human review gold segment 字段非法")
                page = pages[page_index]
                _validate_gold_bbox(
                    segment.get("paperwright_bbox"),
                    page_width=float(page["width"]),
                    page_height=float(page["height"]),
                )
    expected_completion = _completion_for_response(task, response)
    ready = expected_completion["ready_for_scoring"]
    if response.get("completion") != expected_completion:
        _fail("GROBID human review completion 与内容不一致")
    if require_complete and (not ready or not response["reviewer"].strip()):
        _fail("GROBID human review 尚未完成或缺少 reviewer")
    return expected_completion


_REVIEW_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GROBID Gold Review</title>
<style>
:root{--bg:#f3f6fa;--surface:#fff;--text:#172033;--muted:#647087;--line:#d9e0ea;--blue:#1268dc;--blue-soft:#eaf3ff;--amber:#d88900;--amber-soft:#fff6e3;--green:#138a62;--red:#c93c46;--violet:#6658d9;--radius:6px;--header:60px}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--text);background:var(--bg);font-size:14px;overflow:hidden}button,input,textarea,select{font:inherit;color:inherit}button{cursor:pointer}.topbar{height:var(--header);display:grid;grid-template-columns:310px 1fr auto;align-items:center;gap:20px;padding:0 22px;background:var(--surface);border-bottom:1px solid var(--line)}.brand{font-size:20px;font-weight:760;letter-spacing:-.02em}.document-name{min-width:0;font-weight:620;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.header-actions{display:flex;align-items:center;gap:8px}.button{height:38px;border:1px solid var(--line);border-radius:var(--radius);background:#fff;padding:0 14px;font-weight:650}.button:hover,.button:focus-visible{border-color:var(--blue);outline:2px solid #1268dc22}.button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.button.active{background:var(--blue-soft);border-color:#8abaff;color:#0754b5}.workspace{height:calc(100vh - var(--header) - 32px);display:grid;grid-template-columns:310px minmax(420px,1fr) 410px}.sidebar,.inspector{background:var(--surface);min-width:0;overflow:hidden}.sidebar{border-right:1px solid var(--line);display:grid;grid-template-rows:auto auto 1fr}.inspector{border-left:1px solid var(--line);overflow-y:auto}.section{padding:14px 16px;border-bottom:1px solid var(--line)}.section-title{font-weight:730;margin-bottom:10px}.filter-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.filter{min-height:34px;padding:4px 7px;background:#fff;border:1px solid var(--line);border-radius:5px;font-size:12px}.filter.active{background:var(--blue);border-color:var(--blue);color:#fff}.filter-count{opacity:.72;margin-left:4px}.claim-list{overflow:auto}.claim-row{width:100%;display:grid;grid-template-columns:32px 1fr 20px;gap:8px;align-items:start;padding:11px 12px;border:0;border-bottom:1px solid #e6ebf2;background:#fff;text-align:left}.claim-row:hover{background:#f7f9fc}.claim-row.active{background:var(--blue-soft);box-shadow:inset 3px 0 var(--blue)}.claim-index{color:var(--muted);font-variant-numeric:tabular-nums}.claim-preview{line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.claim-state{width:15px;height:15px;border:1.5px solid #a9b4c4;border-radius:50%;margin-top:2px}.claim-state.done{border-color:var(--green);background:radial-gradient(circle,var(--green) 0 43%,transparent 47%)}.canvas-area{min-width:0;display:grid;grid-template-rows:48px 1fr;background:#e9edf3}.canvas-toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 16px;background:#fff;border-bottom:1px solid var(--line)}.segment-nav{display:flex;align-items:center;gap:8px}.segment-nav button{width:30px;height:30px;border:1px solid var(--line);background:#fff;border-radius:5px}.page-stage{overflow:auto;padding:18px;display:flex;align-items:flex-start;justify-content:center}.page-wrap{position:relative;line-height:0;background:#fff;border:1px solid #c8d0dc;box-shadow:0 2px 8px #17203318;max-width:100%}.page-wrap img{display:block;max-width:100%;max-height:calc(100vh - 142px);width:auto;height:auto}.bbox{position:absolute;border:2px solid var(--blue);background:#1268dc12;pointer-events:none}.bbox.native{border-color:var(--amber);background:#f3a90016;border-width:2px}.bbox-label{position:absolute;top:-19px;left:-2px;padding:2px 5px;color:#fff;background:var(--blue);font-size:10px;line-height:14px;white-space:nowrap}.bbox.native .bbox-label{background:var(--amber)}.inspector-head{position:sticky;top:0;z-index:3;background:#fff;padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px}.progress{font-weight:720}.type-label{font-size:12px;color:var(--blue);font-weight:700;text-transform:uppercase;letter-spacing:.04em}.field{padding:14px 16px;border-bottom:1px solid var(--line)}.field label,.field-title{display:block;font-size:12px;color:var(--muted);font-weight:700;margin-bottom:7px}.text-box{padding:10px 11px;border:1px solid var(--line);border-radius:var(--radius);line-height:1.45;white-space:pre-wrap;max-height:180px;overflow:auto}.text-box.grobid{border-color:#87b9ff;background:#f8fbff}.text-box.native{border-color:#efbc5c;background:#fffaf0}.score-table{width:100%;border-collapse:collapse;font-size:12px}.score-table th,.score-table td{border:1px solid var(--line);padding:7px;text-align:center}.score-table th{font-weight:650;color:var(--muted);background:#f7f9fc}.note{width:100%;min-height:78px;resize:vertical;border:1px solid var(--line);border-radius:var(--radius);padding:9px 10px;line-height:1.4}.note:focus{outline:2px solid #1268dc33;border-color:var(--blue)}.labels{display:grid;gap:6px}.label-choice{display:grid;grid-template-columns:22px 1fr 22px;align-items:center;min-height:40px;padding:7px 9px;border:1px solid var(--line);border-radius:var(--radius);background:#fff;text-align:left}.label-choice.active{border-color:var(--blue);background:var(--blue-soft)}.label-choice .key{color:var(--muted);text-align:right;font-size:12px}.radio{width:16px;height:16px;border:1.5px solid #98a5b6;border-radius:50%}.active .radio{border:5px solid var(--blue)}.review-nav{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:14px 16px}.statusbar{height:32px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;background:#fff;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.save-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:7px}.hidden{display:none!important}.gold-workspace{height:calc(100vh - var(--header) - 32px);overflow:auto;padding:28px;background:#f3f6fa}.gold-shell{max-width:1080px;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:var(--radius)}.gold-header{padding:22px 24px;border-bottom:1px solid var(--line)}.gold-header h1{margin:0 0 8px;font-size:22px}.gold-header p{margin:0;color:var(--muted);line-height:1.5}.gold-type{padding:18px 24px;border-bottom:1px solid var(--line)}.gold-type-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.gold-type h2{margin:0;font-size:17px}.gold-status{height:34px;border:1px solid var(--line);border-radius:5px;background:#fff;padding:0 8px}.gold-form{display:grid;grid-template-columns:130px 1fr repeat(4,80px) auto;gap:7px;margin-top:12px}.gold-form input{min-width:0;height:36px;border:1px solid var(--line);border-radius:5px;padding:0 8px}.gold-units{margin-top:10px}.gold-unit{display:grid;grid-template-columns:80px 1fr auto;gap:10px;padding:9px 0;border-top:1px solid #e8edf3}.gold-unit button{border:0;background:none;color:var(--red)}.empty{color:var(--muted);padding:10px 0}@media(max-width:1100px){.workspace{grid-template-columns:250px minmax(360px,1fr) 350px}.topbar{grid-template-columns:250px 1fr auto}.gold-form{grid-template-columns:110px 1fr repeat(2,72px)}.gold-form .add-gold{grid-column:span 2}}@media(max-width:820px){body{overflow:auto}.topbar{position:sticky;top:0;z-index:10;grid-template-columns:1fr auto;height:auto;min-height:60px}.document-name{display:none}.workspace{height:auto;grid-template-columns:1fr}.sidebar{display:none}.canvas-area{min-height:70vh}.inspector{border-left:0;border-top:1px solid var(--line)}.statusbar{position:sticky;bottom:0}.gold-workspace{height:auto;padding:12px}.gold-form{grid-template-columns:1fr 1fr}.gold-form input:nth-child(2){grid-column:span 2}}
body{display:grid;grid-template-rows:var(--header) minmax(0,1fr) 32px}.topbar{height:auto}.workspace,.gold-workspace{height:auto;min-height:0}.claim-state.done{border:4px solid var(--green);background:none}.statusbar{position:relative;z-index:20;pointer-events:none}.review-nav{position:sticky;bottom:32px;z-index:2;background:#fff;border-top:1px solid var(--line)}.gold-grid{max-width:1320px;margin:0 auto;display:grid;grid-template-columns:minmax(620px,1fr) 390px;gap:18px;align-items:start}.gold-shell{max-width:none;margin:0}.gold-preview{position:sticky;top:0;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:14px}.gold-preview-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;font-weight:720}.gold-preview select,.gold-form select{height:36px;border:1px solid var(--line);border-radius:5px;background:#fff;padding:0 8px}.gold-preview img{display:block;width:100%;height:auto;border:1px solid #c8d0dc}@media(max-width:1100px){.gold-grid{grid-template-columns:1fr}.gold-preview{position:static;order:-1;max-width:500px}}
.gold-form{grid-template-columns:180px 120px minmax(220px,1fr) auto}.gold-form input,.gold-form select{height:38px}.gold-unit{display:block;padding:12px 0}.gold-unit-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.gold-unit-actions{display:flex;gap:10px}.gold-unit-actions button,.gold-fragment button{border:0;background:none;color:var(--red);padding:3px}.gold-unit-actions .merge{color:var(--blue)}.gold-fragments{margin-top:8px;border-left:3px solid var(--blue-soft);padding-left:10px}.gold-fragment{display:grid;grid-template-columns:58px 1fr auto;gap:10px;align-items:start;padding:7px 0;border-top:1px solid #edf1f6;line-height:1.4}.gold-help{margin:10px 0 0;color:var(--muted);font-size:12px;line-height:1.45}@media(max-width:900px){.gold-form{grid-template-columns:1fr 1fr}.gold-form input[name="text"]{grid-column:span 2}.gold-form .add-gold{grid-column:span 2}}
</style>
</head>
<body>
<header class="topbar"><div class="brand">GROBID Gold Review</div><div class="document-name" id="documentName"></div><div class="header-actions"><button class="button" id="claimsMode">Claims</button><button class="button" id="goldMode">Gold units</button><button class="button" id="importButton">Import JSON</button><button class="button primary" id="exportButton">Export JSON</button><input class="hidden" type="file" id="importFile" accept="application/json"></div></header>
<main class="workspace" id="claimsWorkspace">
<aside class="sidebar"><section class="section"><div class="section-title">Filters</div><div class="filter-grid" id="filters"></div></section><section class="section"><div class="section-title" id="claimCount"></div></section><div class="claim-list" id="claimList"></div></aside>
<section class="canvas-area"><div class="canvas-toolbar"><div id="pageNumber"></div><div class="segment-nav"><button id="previousSegment" aria-label="Previous segment">←</button><span id="segmentNumber"></span><button id="nextSegment" aria-label="Next segment">→</button></div></div><div class="page-stage"><div class="page-wrap" id="pageWrap"><img id="pageImage" alt="Scientific paper page under review"></div></div></section>
<aside class="inspector"><div class="inspector-head"><div><div class="progress" id="progress"></div><div class="type-label" id="claimType"></div></div><div id="claimId"></div></div><section class="field"><div class="field-title">GROBID text</div><div class="text-box grobid" id="grobidText"></div></section><section class="field"><div class="field-title">Aligned native text</div><div class="text-box native" id="nativeText"></div></section><section class="field"><div class="field-title">Alignment for selected segment</div><table class="score-table"><thead><tr><th>Text score</th><th>Geometry score</th><th>Matches</th></tr></thead><tbody><tr><td id="textScore"></td><td id="geometryScore"></td><td id="matchCount"></td></tr></tbody></table></section><section class="field"><label for="reviewer">Reviewer</label><input class="note" style="min-height:38px" id="reviewer" placeholder="Required before final scoring"><label for="reviewNote" style="margin-top:12px">Reviewer note</label><textarea class="note" id="reviewNote" placeholder="Optional boundary or role note"></textarea></section><section class="field"><div class="field-title">Annotation (select one)</div><div class="labels" id="labels"></div></section><div class="review-nav"><button class="button" id="previousClaim">← Previous</button><button class="button primary" id="nextClaim">Next →</button></div></aside>
</main>
<main class="gold-workspace hidden" id="goldWorkspace"><div class="gold-grid"><div class="gold-shell"><div class="gold-header"><h1>Recall gold units</h1><p>Independently enumerate units visible in the PDF. Do not infer missing units from GROBID claims. Mark each type complete only after checking the whole paper.</p></div><div id="goldTypes"></div></div><aside class="gold-preview"><div class="gold-preview-head"><span>Whole-paper page check</span><select id="goldPageSelect"></select></div><img id="goldPageImage" alt="Paper page for independent gold enumeration"></aside></div></main>
<footer class="statusbar"><div><span class="save-dot"></span><span id="saveStatus">Changes are saved locally.</span></div><div id="completionStatus"></div></footer>
<script>
const task=__TASK_JSON__;
const initialResponse=__RESPONSE_JSON__;
const imageSources=__IMAGE_SOURCES_JSON__;
const taskHash=__TASK_HASH_JSON__;
const responseVersion="paperwright-grobid-human-review-v0.2";
const legacyResponseVersion="paperwright-grobid-human-review-v0.1";
const labels=["correct","partial","wrong_role","unsupported","uncertain"];
const labelNames={correct:"Correct",partial:"Partial",wrong_role:"Wrong role",unsupported:"Unsupported",uncertain:"Uncertain"};
const goldTypes=["title","abstract","section_heading","figure_caption","table_caption","reference"];
const storageKey=`paperwright-grobid-review:${taskHash}`;
let response=loadResponse();let current=0;let segmentIndex=0;let activeFilter="all";let mode="claims";
const $=id=>document.getElementById(id);
const annotations=()=>new Map(response.claim_annotations.map(item=>[item.claim_id,item]));
function migrateResponse(value){const migrated=structuredClone(value);if(migrated.contract_version===legacyResponseVersion){goldTypes.forEach(type=>{migrated.gold_enumeration[type].units.forEach(unit=>{unit.segments=[{page_index:unit.page_index,text:unit.text,paperwright_bbox:unit.paperwright_bbox??null}];delete unit.page_index;delete unit.text;delete unit.paperwright_bbox})});migrated.contract_version=responseVersion}if(migrated.contract_version!==responseVersion)throw new Error("Unsupported response contract");const labeled=migrated.claim_annotations.filter(item=>item.label!==null).length;const complete=goldTypes.filter(type=>["complete","not_applicable"].includes(migrated.gold_enumeration[type].status)).length;migrated.completion={claim_count:task.claims.length,claims_labeled:labeled,gold_types_complete:complete,ready_for_scoring:labeled===task.claims.length&&complete===goldTypes.length};return migrated}
function loadResponse(){try{const saved=localStorage.getItem(storageKey);if(saved){const value=migrateResponse(JSON.parse(saved));if(value.task_sha256===taskHash)return value}}catch(error){}return migrateResponse(initialResponse)}
function completion(){const labeled=response.claim_annotations.filter(item=>item.label!==null).length;const complete=goldTypes.filter(type=>["complete","not_applicable"].includes(response.gold_enumeration[type].status)).length;return{claim_count:task.claims.length,claims_labeled:labeled,gold_types_complete:complete,ready_for_scoring:labeled===task.claims.length&&complete===goldTypes.length}}
function save(){response.reviewer=$("reviewer").value;response.completion=completion();localStorage.setItem(storageKey,JSON.stringify(response));$("saveStatus").textContent="Changes are saved locally.";renderCompletion()}
function uniqueTexts(values){return [...new Set(values.filter(Boolean).map(v=>v.trim()).filter(Boolean))]}
function claimText(claim){return uniqueTexts(claim.segments.map(segment=>segment.text)).join("\n")}
function nativeText(claim){return uniqueTexts(claim.segments.flatMap(segment=>segment.alignments.map(item=>item.native_text))).join("\n")||"No aligned native text"}
function typeLabel(value){return value.replaceAll("_"," ")}
function renderFilters(){const counts={};task.claims.forEach(c=>counts[c.claim_type]=(counts[c.claim_type]||0)+1);const items=[["all","All"],["unreviewed","Unreviewed"],...Object.keys(counts).sort().map(type=>[type,typeLabel(type)])];$("filters").innerHTML="";items.forEach(([value,name])=>{const button=document.createElement("button");button.className=`filter ${activeFilter===value?"active":""}`;const count=value==="all"?task.claims.length:value==="unreviewed"?response.claim_annotations.filter(a=>a.label===null).length:counts[value];button.innerHTML=`${name}<span class="filter-count">${count}</span>`;button.onclick=()=>{activeFilter=value;renderFilters();renderClaimList()};$("filters").append(button)})}
function visibleClaims(){const map=annotations();return task.claims.map((claim,index)=>({claim,index})).filter(({claim})=>activeFilter==="all"||(activeFilter==="unreviewed"&&map.get(claim.claim_id).label===null)||claim.claim_type===activeFilter)}
function renderClaimList(){const list=$("claimList");list.innerHTML="";const map=annotations();visibleClaims().forEach(({claim,index})=>{const row=document.createElement("button");row.className=`claim-row ${index===current?"active":""}`;row.innerHTML=`<span class="claim-index">${index+1}</span><span class="claim-preview"></span><span class="claim-state ${map.get(claim.claim_id).label?"done":""}"></span>`;row.querySelector(".claim-preview").textContent=claimText(claim);row.onclick=()=>selectClaim(index);list.append(row)});$("claimCount").textContent=`Claims (${visibleClaims().length} shown)`;requestAnimationFrame(()=>list.querySelector(".active")?.scrollIntoView({block:"nearest"}))}
function pageRecord(pageIndex){return task.page_images.find(page=>page.page_index===pageIndex)}
function addBox(bbox,page,kind,label){if(!bbox)return;const div=document.createElement("div");div.className=`bbox ${kind}`;div.style.left=`${bbox.x/page.width*100}%`;div.style.top=`${bbox.y/page.height*100}%`;div.style.width=`${bbox.width/page.width*100}%`;div.style.height=`${bbox.height/page.height*100}%`;const marker=document.createElement("span");marker.className="bbox-label";marker.textContent=label;div.append(marker);$("pageWrap").append(div)}
function renderCanvas(claim){const segment=claim.segments[Math.min(segmentIndex,claim.segments.length-1)];const page=pageRecord(segment.page_index);$("pageImage").src=imageSources[String(page.page_index)];$("pageNumber").textContent=`Page ${page.page_index+1}`;$("segmentNumber").textContent=`Segment ${segmentIndex+1} / ${claim.segments.length}`;$("previousSegment").disabled=segmentIndex===0;$("nextSegment").disabled=segmentIndex===claim.segments.length-1;$("pageImage").onload=()=>{document.querySelectorAll(".bbox").forEach(node=>node.remove());claim.segments.filter(item=>item.page_index===page.page_index).forEach(item=>addBox(item.paperwright_bbox,page,"","GROBID"));segment.alignments.forEach(item=>addBox(item.native_bbox,page,"native","Native"))}}
function renderInspector(claim){const annotation=annotations().get(claim.claim_id);$("progress").textContent=`Selected claim ${current+1} / ${task.claims.length}`;$("claimType").textContent=typeLabel(claim.claim_type);$("claimId").textContent=claim.claim_id;$("grobidText").textContent=claimText(claim);$("nativeText").textContent=nativeText(claim);$("reviewNote").value=annotation.note;const segment=claim.segments[segmentIndex];const matches=segment.alignments;const best=(key)=>matches.length?Math.max(...matches.map(item=>Number(item[key])||0)).toFixed(3):"—";$("textScore").textContent=best("text_score");$("geometryScore").textContent=best("geometry_score");$("matchCount").textContent=matches.length;$("labels").innerHTML="";labels.forEach((label,index)=>{const button=document.createElement("button");button.className=`label-choice ${annotation.label===label?"active":""}`;button.innerHTML=`<span class="radio"></span><span>${labelNames[label]}</span><span class="key">${index+1}</span>`;button.onclick=()=>setLabel(label);$("labels").append(button)})}
function renderClaim(){const claim=task.claims[current];renderCanvas(claim);renderInspector(claim);renderClaimList();renderFilters()}
function selectClaim(index){current=Math.max(0,Math.min(task.claims.length-1,index));segmentIndex=0;renderClaim()}
function setLabel(label){response.claim_annotations[current].label=label;save();renderClaim()}
function stepClaim(delta){selectClaim(current+delta)}
function renderCompletion(){const c=completion();$("completionStatus").textContent=`${c.claims_labeled}/${c.claim_count} claims · ${c.gold_types_complete}/${goldTypes.length} gold types${c.ready_for_scoring?" · Ready for validation":""}`}
function pageOptions(selected){return task.page_images.map(page=>`<option value="${page.page_index}" ${page.page_index===selected?"selected":""}>Page ${page.page_index+1}</option>`).join("")}
function renderGoldPreview(pageIndex){const page=pageRecord(Number(pageIndex));if(!page)return;$("goldPageSelect").value=String(page.page_index);$("goldPageImage").src=imageSources[String(page.page_index)]}
function nextGoldSequence(units){const values=units.map(unit=>Number(unit.gold_unit_id.split(":").at(-1))).filter(Number.isFinite);return(values.length?Math.max(...values):0)+1}
function goldUnitPages(unit){return[...new Set(unit.segments.map(segment=>segment.page_index+1))]}
function renderGold(){const root=$("goldTypes");root.innerHTML="";if(!$("goldPageSelect").options.length){$("goldPageSelect").innerHTML=pageOptions(task.page_images[0].page_index);$("goldPageSelect").onchange=event=>renderGoldPreview(event.target.value)}renderGoldPreview($("goldPageSelect").value||task.page_images[0].page_index);goldTypes.forEach(type=>{const record=response.gold_enumeration[type];const section=document.createElement("section");section.className="gold-type";section.innerHTML=`<div class="gold-type-head"><h2>${typeLabel(type)}</h2><select class="gold-status"><option value="in_progress">In progress</option><option value="complete">Complete</option><option value="not_applicable">Not applicable</option></select></div><p class="gold-help">Each row is one semantic unit. If it continues on another page, choose that existing unit under “Attach to” and add another fragment.</p><form class="gold-form"><select name="target" aria-label="Attach fragment to a semantic unit"></select><select name="page" required>${pageOptions(Number($("goldPageSelect").value)||task.page_images[0].page_index)}</select><input name="text" placeholder="Visible text on this page" required><button class="button add-gold" type="submit">Add fragment</button></form><div class="gold-units"></div>`;const status=section.querySelector("select.gold-status");status.value=record.status;status.onchange=()=>{record.status=status.value;if(status.value==="not_applicable")record.units=[];save();renderGold()};const target=section.querySelector('select[name="target"]');target.innerHTML='<option value="__new__">New semantic unit</option>'+record.units.map((unit,index)=>`<option value="${unit.gold_unit_id}">Attach to unit ${index+1} · p. ${goldUnitPages(unit).join(", ")}</option>`).join("");const pageSelect=section.querySelector('select[name="page"]');pageSelect.onchange=()=>renderGoldPreview(pageSelect.value);section.querySelector("form").onsubmit=event=>{event.preventDefault();const form=new FormData(event.currentTarget);const segment={page_index:Number(form.get("page")),text:String(form.get("text")).trim(),paperwright_bbox:null};const targetId=String(form.get("target"));if(targetId==="__new__"){const sequence=nextGoldSequence(record.units);record.units.push({gold_unit_id:`${task.document_id}:${type}:${String(sequence).padStart(4,"0")}`,claim_type:type,segments:[segment],note:""})}else{const unit=record.units.find(item=>item.gold_unit_id===targetId);if(!unit)return;unit.segments.push(segment)}record.status="in_progress";save();renderGold()};const units=section.querySelector(".gold-units");if(!record.units.length)units.innerHTML='<div class="empty">No semantic units recorded.</div>';record.units.forEach((unit,index)=>{const row=document.createElement("div");row.className="gold-unit";row.innerHTML='<div class="gold-unit-head"><strong></strong><div class="gold-unit-actions"></div></div><div class="gold-fragments"></div>';row.querySelector("strong").textContent=`Unit ${index+1} · ${unit.segments.length} fragment${unit.segments.length===1?"":"s"} · p. ${goldUnitPages(unit).join(", ")}`;const actions=row.querySelector(".gold-unit-actions");if(index>0){const merge=document.createElement("button");merge.type="button";merge.className="merge";merge.textContent="Merge into previous";merge.onclick=()=>{record.units[index-1].segments.push(...unit.segments);record.units.splice(index,1);record.status="in_progress";save();renderGold()};actions.append(merge)}const remove=document.createElement("button");remove.type="button";remove.textContent="Remove unit";remove.onclick=()=>{record.units.splice(index,1);record.status="in_progress";save();renderGold()};actions.append(remove);const fragments=row.querySelector(".gold-fragments");unit.segments.forEach((segment,segmentPosition)=>{const fragment=document.createElement("div");fragment.className="gold-fragment";fragment.innerHTML='<strong></strong><span></span><button type="button">Remove</button>';fragment.querySelector("strong").textContent=`p. ${segment.page_index+1}`;fragment.querySelector("span").textContent=segment.text;fragment.querySelector("button").onclick=()=>{if(unit.segments.length===1)record.units.splice(index,1);else unit.segments.splice(segmentPosition,1);record.status="in_progress";save();renderGold()};fragments.append(fragment)});units.append(row)});root.append(section)})}
function setMode(value){mode=value;$("claimsWorkspace").classList.toggle("hidden",value!=="claims");$("goldWorkspace").classList.toggle("hidden",value!=="gold");$("claimsMode").classList.toggle("active",value==="claims");$("goldMode").classList.toggle("active",value==="gold");if(value==="gold")renderGold()}
function exportResponse(){save();const blob=new Blob([JSON.stringify(response,null,2)+"\n"],{type:"application/json"});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`${task.document_id}.human-review.json`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000)}
function importResponse(file){const reader=new FileReader();reader.onload=()=>{try{const value=migrateResponse(JSON.parse(reader.result));if(value.task_sha256!==taskHash||value.document_id!==task.document_id)throw new Error("Response is bound to another task");const ids=value.claim_annotations.map(item=>item.claim_id);if(ids.join("\n")!==task.claims.map(item=>item.claim_id).join("\n"))throw new Error("Claim IDs do not match");response=value;localStorage.setItem(storageKey,JSON.stringify(response));$("reviewer").value=response.reviewer||"";renderClaim();renderCompletion();if(mode==="gold")renderGold();alert("Review imported and migrated when needed.")}catch(error){alert(`Import failed: ${error.message}`)}};reader.readAsText(file)}
$("documentName").textContent=task.document_id;$("reviewer").value=response.reviewer||"";$("reviewer").oninput=save;$("reviewNote").oninput=()=>{response.claim_annotations[current].note=$("reviewNote").value;save()};$("previousClaim").onclick=()=>stepClaim(-1);$("nextClaim").onclick=()=>stepClaim(1);$("previousSegment").onclick=()=>{segmentIndex--;renderClaim()};$("nextSegment").onclick=()=>{segmentIndex++;renderClaim()};$("claimsMode").onclick=()=>setMode("claims");$("goldMode").onclick=()=>setMode("gold");$("exportButton").onclick=exportResponse;$("importButton").onclick=()=>$("importFile").click();$("importFile").onchange=event=>event.target.files[0]&&importResponse(event.target.files[0]);document.addEventListener("keydown",event=>{if(["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName))return;if(event.key>="1"&&event.key<="5")setLabel(labels[Number(event.key)-1]);if(event.key==="ArrowLeft")stepClaim(-1);if(event.key==="ArrowRight")stepClaim(1)});setMode("claims");renderClaim();renderCompletion();
</script>
</body>
</html>
'''


def _safe_script_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("</", "<\\/")


def render_grobid_human_review_html(
    task: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    image_sources: Mapping[str, str],
) -> str:
    """Render one dependency-free, local-first review application."""

    validate_grobid_audit_task(task)
    validate_grobid_human_review(task, response)
    expected_pages = {str(page["page_index"]) for page in task["page_images"]}
    if set(image_sources) != expected_pages or not all(
        isinstance(value, str) and value for value in image_sources.values()
    ):
        _fail("GROBID human review image_sources 与 task 不一致")
    return (
        _REVIEW_HTML.replace("__TASK_JSON__", _safe_script_json(task))
        .replace("__RESPONSE_JSON__", _safe_script_json(response))
        .replace("__IMAGE_SOURCES_JSON__", _safe_script_json(image_sources))
        .replace(
            "__TASK_HASH_JSON__",
            _safe_script_json(grobid_audit_task_sha256(task)),
        )
    )


def render_grobid_human_review_index(
    documents: list[Mapping[str, Any]],
) -> str:
    """Render a compact entry page for the per-document offline reviewers."""

    rows = "".join(
        "<tr>"
        f"<td><a href=\"{html.escape(str(item['html']))}\">"
        f"{html.escape(str(item['document_id']))}</a></td>"
        f"<td>{int(item['claim_count'])}</td>"
        f"<td>{html.escape(', '.join(item['claim_types']))}</td>"
        "</tr>"
        for item in documents
    )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GROBID Gold Review</title><style>body{{margin:0;background:#f3f6fa;color:#172033;font:14px Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1100px;margin:52px auto;padding:0 24px}}h1{{font-size:30px;margin:0 0 10px}}p{{color:#647087;line-height:1.6}}table{{width:100%;margin-top:28px;border-collapse:collapse;background:#fff;border:1px solid #d9e0ea}}th,td{{padding:13px 15px;border-bottom:1px solid #d9e0ea;text-align:left}}th{{font-size:12px;color:#647087;background:#f8fafc}}a{{color:#1268dc;font-weight:700;text-decoration:none}}a:hover{{text-decoration:underline}}code{{background:#eaf0f7;padding:2px 5px;border-radius:4px}}</style></head><body><main><h1>GROBID Gold Review</h1><p>Blind claim annotation and independent recall enumeration. Open one document below. Work is autosaved in this browser; use <code>Export JSON</code> after each session.</p><table><thead><tr><th>Document</th><th>Claims</th><th>Claim types</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>'''


__all__ = [
    "GROBID_HUMAN_REVIEW_LEGACY_VERSION",
    "GROBID_HUMAN_REVIEW_MANIFEST_VERSION",
    "GROBID_HUMAN_REVIEW_VERSION",
    "GOLD_STATUSES",
    "RECALL_GOLD_TYPES",
    "REVIEW_LABELS",
    "build_grobid_human_review_template",
    "grobid_audit_task_sha256",
    "merge_grobid_gold_units",
    "migrate_grobid_human_review_v01",
    "render_grobid_human_review_html",
    "render_grobid_human_review_index",
    "validate_grobid_audit_task",
    "validate_grobid_human_review",
]
