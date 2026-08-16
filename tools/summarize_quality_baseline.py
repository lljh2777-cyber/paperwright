#!/usr/bin/env python3
"""Aggregate a privacy-safe scientific-paper quality baseline.

The input PDFs and full conversion outputs stay outside the repository.  This
tool consumes PaperWright batch manifests, routing plans and compact reviewer
annotations, then writes only inventory metadata and aggregate findings.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


CONTRACT_VERSION = "paperwright-quality-baseline-v0.1"
DIMENSIONS = (
    "text_integrity",
    "reading_order",
    "section_structure",
    "visual_completeness",
    "caption_binding",
    "furniture_exclusion",
    "provenance",
    "uncertainty_handling",
)
RESULTS = {"pass", "minor", "major", "not_assessed"}
LIKELY_LAYERS = {
    "extraction",
    "evidence",
    "routing",
    "rule",
    "text_model",
    "visual_model",
    "validation",
    "projection",
    "unknown",
}
RECOMMENDED_ACTIONS = {
    "keep_rule",
    "join_blocks",
    "split_block",
    "reorder",
    "bind_caption",
    "exclude_furniture",
    "render_visual",
    "request_text_judgment",
    "request_visual",
    "paper_recipe",
    "human_required",
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON {path}: {exc}") from exc


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"拒绝覆盖已有评测产物: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _annotation_status(value: Any) -> str:
    if isinstance(value, str):
        status = value
    elif isinstance(value, Mapping):
        status = value.get("status", value.get("result", "not_assessed"))
    else:
        status = "not_assessed"
    return status if status in RESULTS else "not_assessed"


def _sampled_pages(value: Any) -> set[int]:
    result: set[int] = set()
    if not isinstance(value, list):
        return result
    for item in value:
        page = item.get("page") if isinstance(item, Mapping) else item
        if isinstance(page, int) and page >= 1:
            result.add(page)
    return result


def _overall_status(value: Any) -> str:
    aliases = {
        "pass": "pass",
        "minor": "minor",
        "minor_issues": "minor",
        "major": "major",
        "major_issues": "major",
        "not_assessed": "not_assessed",
    }
    return aliases.get(str(value), "not_assessed")


def _annotation_index(annotation_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(annotation_dir.glob("*.json")):
        value = _load_json(path)
        if not isinstance(value, dict):
            raise ValueError(f"标注必须是 JSON object: {path}")
        filename = value.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"标注缺少 filename: {path}")
        if filename in index:
            raise ValueError(f"同一论文存在重复标注: {filename}")
        value["_annotation_path"] = path.name
        index[filename] = value
    return index


def _validate_annotation(
    annotation: Mapping[str, Any],
    *,
    filename: str,
    source_sha256: str,
    page_count: int,
) -> None:
    required = {
        "filename",
        "sha256",
        "page_count",
        "native_text",
        "layout_profile",
        "features",
        "sampled_pages",
        "routing_observations",
        "dimension_results",
        "issues",
        "hallucination_count",
        "overall_status",
        "reviewer_model",
    }
    missing = sorted(required - annotation.keys())
    if missing:
        raise ValueError(f"标注缺少字段 {filename}: {', '.join(missing)}")
    if annotation["sha256"] != source_sha256:
        raise ValueError(f"标注 SHA-256 与 batch 不一致: {filename}")
    if annotation["page_count"] != page_count:
        raise ValueError(f"标注页数与 manifest 不一致: {filename}")
    if not isinstance(annotation["reviewer_model"], str):
        raise ValueError(f"reviewer_model 必须是字符串: {filename}")
    hallucinations = annotation["hallucination_count"]
    if not isinstance(hallucinations, int) or hallucinations < 0:
        raise ValueError(f"hallucination_count 非法: {filename}")

    dimensions = annotation["dimension_results"]
    if not isinstance(dimensions, Mapping):
        raise ValueError(f"dimension_results 必须是 object: {filename}")
    if set(dimensions) != set(DIMENSIONS):
        raise ValueError(f"dimension_results 维度不完整: {filename}")
    for dimension in DIMENSIONS:
        raw_result = dimensions[dimension]
        if isinstance(raw_result, str):
            status = raw_result
        elif isinstance(raw_result, Mapping):
            status = raw_result.get("status", raw_result.get("result"))
        else:
            status = None
        if status not in RESULTS:
            raise ValueError(f"维度结果非法 {filename}: {dimension}")

    sampled = _sampled_pages(annotation["sampled_pages"])
    if any(page > page_count for page in sampled):
        raise ValueError(f"抽样页超出文档页数: {filename}")
    issues = annotation["issues"]
    if not isinstance(issues, list):
        raise ValueError(f"issues 必须是数组: {filename}")
    issue_fields = {
        "page",
        "category",
        "severity",
        "source_evidence",
        "observed_output",
        "likely_layer",
        "recommended_action",
        "confidence",
    }
    for index, issue in enumerate(issues, start=1):
        if not isinstance(issue, Mapping) or not issue_fields.issubset(issue):
            raise ValueError(f"issue 字段不完整 {filename}: #{index}")
        if not isinstance(issue["page"], int) or not 1 <= issue["page"] <= page_count:
            raise ValueError(f"issue 页码非法 {filename}: #{index}")
        if issue["severity"] not in {"minor", "major"}:
            raise ValueError(f"issue severity 非法 {filename}: #{index}")
        if issue["likely_layer"] not in LIKELY_LAYERS:
            raise ValueError(f"issue likely_layer 非法 {filename}: #{index}")
        if issue["recommended_action"] not in RECOMMENDED_ACTIONS:
            raise ValueError(f"issue recommended_action 非法 {filename}: #{index}")


def _routing_index(layout_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(layout_root.glob("*/routing.json")):
        value = _load_json(path)
        source_sha256 = value.get("source_sha256")
        if isinstance(source_sha256, str):
            index[source_sha256] = value
    return index


def _codes(records: Any) -> Counter[str]:
    result: Counter[str] = Counter()
    if not isinstance(records, list):
        return result
    for item in records:
        if isinstance(item, Mapping):
            code = item.get("code", "unknown")
            result[str(code)] += 1
    return result


def _asset_counts(manifest: Mapping[str, Any]) -> dict[str, int]:
    return {
        key: len(manifest.get(key, []))
        for key in ("images", "figures", "tables", "equations", "figure_rejections")
        if isinstance(manifest.get(key, []), list)
    }


def build_baseline(
    batch_root: Path,
    layout_root: Path,
    annotation_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = _load_json(batch_root / "batch_summary.json")
    annotations = _annotation_index(annotation_dir)
    routing = _routing_index(layout_root)

    documents: list[dict[str, Any]] = []
    pipeline_statuses: Counter[str] = Counter()
    warning_codes: Counter[str] = Counter()
    degraded_codes: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    routing_counts: Counter[str] = Counter()
    dimension_results = {name: Counter() for name in DIMENSIONS}
    issue_categories: Counter[str] = Counter()
    issue_severities: Counter[str] = Counter()
    likely_layers: Counter[str] = Counter()
    recommended_actions: Counter[str] = Counter()
    overall_statuses: Counter[str] = Counter()
    sampled_pages: set[tuple[str, int]] = set()
    issue_count = 0
    hallucination_count = 0

    for record in batch.get("documents", []):
        filename = record["input_name"]
        source_sha256 = record["input_sha256"]
        output_dir = record.get("output_dir")
        manifest: dict[str, Any] = {}
        if isinstance(output_dir, str):
            manifest_path = batch_root / output_dir / "manifest.json"
            if manifest_path.is_file():
                manifest = _load_json(manifest_path)

        route = routing.get(source_sha256, {})
        route_summary = route.get("summary", {})
        if isinstance(route_summary, Mapping):
            routing_counts.update(
                {str(key): int(value) for key, value in route_summary.items()}
            )

        warning = _codes(manifest.get("warnings", []))
        degraded = _codes(manifest.get("degraded", []))
        assets = _asset_counts(manifest)
        warning_codes.update(warning)
        degraded_codes.update(degraded)
        asset_counts.update(assets)
        pipeline_status = str(manifest.get("status", record.get("status", "unknown")))
        pipeline_statuses[pipeline_status] += 1

        annotation = annotations.pop(filename, None)
        sampled = set()
        annotation_path = None
        if annotation is not None:
            annotation_path = annotation.pop("_annotation_path")
            _validate_annotation(
                annotation,
                filename=filename,
                source_sha256=source_sha256,
                page_count=int(manifest.get("page_count", route.get("page_count", 0))),
            )
            sampled = _sampled_pages(annotation.get("sampled_pages"))
            sampled_pages.update((source_sha256, page) for page in sampled)
            dimensions = annotation.get("dimension_results", {})
            if not isinstance(dimensions, Mapping):
                dimensions = {}
            for dimension in DIMENSIONS:
                dimension_results[dimension][
                    _annotation_status(dimensions.get(dimension))
                ] += 1
            issues = annotation.get("issues", [])
            if not isinstance(issues, list):
                raise ValueError(f"issues 必须是数组: {filename}")
            for issue in issues:
                if not isinstance(issue, Mapping):
                    continue
                issue_count += 1
                issue_categories[str(issue.get("category", "unknown"))] += 1
                issue_severities[str(issue.get("severity", "unknown"))] += 1
                likely_layers[str(issue.get("likely_layer", "unknown"))] += 1
                recommended_actions[
                    str(issue.get("recommended_action", "unknown"))
                ] += 1
            raw_hallucinations = annotation.get("hallucination_count", 0)
            if isinstance(raw_hallucinations, int) and raw_hallucinations >= 0:
                hallucination_count += raw_hallucinations
            overall_statuses[_overall_status(annotation.get("overall_status"))] += 1

        documents.append(
            {
                "filename": filename,
                "sha256": source_sha256,
                "size_bytes": record.get("input_size_bytes"),
                "page_count": manifest.get("page_count", route.get("page_count")),
                "pipeline_status": pipeline_status,
                "output_dir": output_dir,
                "warning_codes": _counter_dict(warning),
                "degraded_codes": _counter_dict(degraded),
                "asset_counts": assets,
                "routing_summary": dict(sorted(route_summary.items()))
                if isinstance(route_summary, Mapping)
                else {},
                "annotation_path": annotation_path,
                "sampled_pages": sorted(sampled),
            }
        )

    if annotations:
        names = ", ".join(sorted(annotations))
        raise ValueError(f"标注 filename 不在 batch summary 中: {names}")

    pages_total = sum(
        int(item["page_count"] or 0) for item in documents
    )
    sampled_page_count = len(sampled_pages)
    route_total = sum(routing_counts.values())
    summary: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "documents_total": len(documents),
        "pages_total": pages_total,
        "annotated_documents": sum(item["annotation_path"] is not None for item in documents),
        "sampled_page_count": sampled_page_count,
        "pipeline_statuses": _counter_dict(pipeline_statuses),
        "warning_codes": _counter_dict(warning_codes),
        "degraded_codes": _counter_dict(degraded_codes),
        "asset_counts": _counter_dict(asset_counts),
        "routing_counts": _counter_dict(routing_counts),
        "routing_rates": {
            key: round(value / route_total, 6) if route_total else 0.0
            for key, value in sorted(routing_counts.items())
        },
        "dimension_results": {
            name: _counter_dict(counter)
            for name, counter in dimension_results.items()
        },
        "issue_count": issue_count,
        "issues_per_100_sampled_pages": (
            round(issue_count * 100 / sampled_page_count, 3)
            if sampled_page_count
            else None
        ),
        "issue_categories": _counter_dict(issue_categories),
        "issue_severities": _counter_dict(issue_severities),
        "likely_layers": _counter_dict(likely_layers),
        "recommended_actions": _counter_dict(recommended_actions),
        "hallucination_count": hallucination_count,
        "overall_statuses": _counter_dict(overall_statuses),
    }
    corpus = {
        "contract_version": CONTRACT_VERSION,
        "batch_summary_sha256": hashlib.sha256(
            (batch_root / "batch_summary.json").read_bytes()
        ).hexdigest(),
        "documents": documents,
    }
    return corpus, summary


def _rows(counter: Mapping[str, Any]) -> Iterable[str]:
    for key, value in counter.items():
        yield f"| `{key}` | {value} |"


def render_failure_taxonomy(summary: Mapping[str, Any]) -> str:
    sections = [
        "# PaperWright 真实论文失败分类 v0.1",
        "",
        "问题按最早可以阻止错误的层归因。以下内容由标注聚合生成，不包含论文正文。",
        "",
        "## 归因层",
        "",
        "| 层 | Issue 数 |",
        "|---|---:|",
        *_rows(summary["likely_layers"]),
        "",
        "## 问题类型",
        "",
        "| 类型 | Issue 数 |",
        "|---|---:|",
        *_rows(summary["issue_categories"]),
        "",
        "## 建议操作",
        "",
        "| 操作 | Issue 数 |",
        "|---|---:|",
        *_rows(summary["recommended_actions"]),
        "",
    ]
    return "\n".join(sections)


def render_report(summary: Mapping[str, Any]) -> str:
    routing = summary["routing_counts"]
    dimensions = summary["dimension_results"]
    lines = [
        "# PaperWright 真实科研论文质量基线 v0.1",
        "",
        f"- 语料：{summary['documents_total']} 篇，{summary['pages_total']} 页",
        f"- 已审阅：{summary['annotated_documents']} 篇，{summary['sampled_page_count']} 个去重抽样页",
        f"- 记录问题：{summary['issue_count']} 个；每 100 抽样页 {summary['issues_per_100_sampled_pages']}",
        f"- 无来源生成/实质改写：{summary['hallucination_count']} 个",
        f"- 文档结论：major {summary['overall_statuses'].get('major', 0)} / minor {summary['overall_statuses'].get('minor', 0)} / pass {summary['overall_statuses'].get('pass', 0)}",
        "",
        "## 当前路由分布",
        "",
        "| Route | 页数 | 比例 |",
        "|---|---:|---:|",
    ]
    for route, count in routing.items():
        rate = summary["routing_rates"].get(route, 0.0)
        lines.append(f"| `{route}` | {count} | {rate:.1%} |")
    lines.extend(["", "## 文档级质量结果", "", "| 维度 | pass | minor | major | not assessed |", "|---|---:|---:|---:|---:|"])
    for dimension in DIMENSIONS:
        values = dimensions[dimension]
        lines.append(
            f"| `{dimension}` | {values.get('pass', 0)} | "
            f"{values.get('minor', 0)} | {values.get('major', 0)} | "
            f"{values.get('not_assessed', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 高频问题与归因",
            "",
            "### Issue 类型",
            "",
            "| 类型 | 数量 |",
            "|---|---:|",
            *_rows(summary["issue_categories"]),
            "",
            "### 最早可阻止错误的层",
            "",
            "| 层 | 数量 |",
            "|---|---:|",
            *_rows(summary["likely_layers"]),
            "",
            "## 自动产物概况",
            "",
            "| 项目 | 数量 |",
            "|---|---:|",
            *_rows(summary["asset_counts"]),
            "",
            "本报告不包含模型价格、费用估算或预算判断。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", required=True, type=Path)
    parser.add_argument("--layout-root", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    corpus, summary = build_baseline(
        args.batch_root.resolve(),
        args.layout_root.resolve(),
        args.annotations.resolve(),
    )
    output_dir = args.output_dir.resolve()
    targets = {
        "corpus.json": _canonical_json(corpus),
        "baseline-summary.json": _canonical_json(summary),
        "failure-taxonomy.md": render_failure_taxonomy(summary),
        "baseline-report.md": render_report(summary),
    }
    existing = [name for name in targets if (output_dir / name).exists()]
    if existing:
        raise SystemExit(f"拒绝覆盖已有评测产物: {', '.join(existing)}")
    for name, content in targets.items():
        _write_new(output_dir / name, content)
    print(
        json.dumps(
            {
                "status": "completed",
                "output_dir": str(output_dir),
                "documents": summary["documents_total"],
                "annotated_documents": summary["annotated_documents"],
                "issues": summary["issue_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
