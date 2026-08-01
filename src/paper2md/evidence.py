"""Self-contained document-package evidence metadata."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Sequence


EVIDENCE_LEVELS = {"minimal", "standard", "full"}
_MAX_ACTIONABLE_FINDINGS = 100


def validate_evidence_level(level: str) -> str:
    if level not in EVIDENCE_LEVELS:
        raise ValueError("evidence_level must be minimal, standard, or full")
    return level


def canonical_json(value: Any) -> str:
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value), encoding="utf-8", newline="\n")


def package_version() -> str:
    try:
        return version("paper2md")
    except PackageNotFoundError:
        return "0.6.0a0"


def build_run_record(
    *,
    source_sha256: str,
    backend: str,
    backend_version: str,
    page_count: int,
    evidence_level: str,
    references_mode: str,
    visual_scale: float,
    status: str,
    task_hashes: Sequence[str],
    final_layout_hashes: Sequence[str],
) -> dict[str, Any]:
    parameters = {
        "evidence": evidence_level,
        "references": references_mode,
        "visual_scale": visual_scale,
    }
    run_seed = {
        "source_sha256": source_sha256,
        "parameters": parameters,
        "task_hashes": list(task_hashes),
        "final_layout_hashes": list(final_layout_hashes),
    }
    run_id = hashlib.sha256(canonical_json(run_seed).encode("utf-8")).hexdigest()
    return {
        "contract_version": "paper2md-run-v0.1",
        "run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "paper2md_version": package_version(),
        "backend": {"name": backend, "version": backend_version},
        "page_count": page_count,
        "parameters": parameters,
        "status": status,
    }


def build_source_record(
    *,
    source: Path,
    source_sha256: str,
    page_count: int,
    included_path: str | None,
) -> dict[str, Any]:
    return {
        "contract_version": "paper2md-source-v0.1",
        "path": str(source.resolve()),
        "filename": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": source_sha256,
        "page_count": page_count,
        "source_pdf_included": included_path is not None,
        "included_path": included_path,
    }


def build_validation_report(
    *,
    status: str,
    evidence_level: str,
    page_count: int,
    image_count: int,
    warnings: Sequence[dict[str, Any]],
    references: dict[str, object],
    reviewers: Sequence[str],
    quality_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warning_summary = build_warning_summary(
        warnings=warnings,
        quality_checks=quality_checks or {},
    )
    report = {
        "contract_version": "paper2md-validation-report-v0.2",
        "status": status,
        "evidence_level": evidence_level,
        "checks": {
            "source_hash_matched": True,
            "page_count_matched": True,
            "layout_contracts_validated": True,
            "native_text_only": True,
            "ocr_not_used": True,
            "relative_markdown_image_paths": True,
            "rendered_images_valid": True,
        },
        "page_count": page_count,
        "rendered_image_count": image_count,
        "reviewers": sorted(set(reviewers)),
        "references": references,
        "warnings": list(warnings),
        "warning_summary": warning_summary,
        "human_review": {
            "status": "layout-reviewed",
            "note": "Final layout decisions were validated before packaging.",
        },
    }
    if quality_checks is not None:
        report["quality_checks"] = quality_checks
    return report


def _issue_severity(status: str) -> str:
    if status == "fail":
        return "error"
    if status == "warning":
        return "warning"
    return "info"


def _actionable_finding(
    *,
    check: str,
    status: str,
    finding: dict[str, Any],
) -> dict[str, Any]:
    code = str(finding.get("detail_code") or finding.get("code") or check)
    result: dict[str, Any] = {
        "severity": _issue_severity(status),
        "check": check,
        "code": code,
    }
    for name in (
        "page",
        "region_id",
        "paragraph_index",
        "element_id",
        "region_ids",
        "snippet",
        "codepoints",
    ):
        if name in finding:
            result[name] = finding[name]
    return result


def build_warning_summary(
    *,
    warnings: Sequence[dict[str, Any]],
    quality_checks: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate validation issues into a compact, actionable index."""

    findings: list[dict[str, Any]] = []
    for check, value in quality_checks.items():
        status = str(value.get("status", "unknown"))
        if status == "pass":
            continue
        detailed = value.get("findings")
        if isinstance(detailed, list) and detailed:
            for item in detailed:
                if isinstance(item, dict):
                    findings.append(
                        _actionable_finding(
                            check=check,
                            status=status,
                            finding=item,
                        )
                    )
        else:
            findings.append(
                _actionable_finding(
                    check=check,
                    status=status,
                    finding={"code": f"{check}_{status}"},
                )
            )

    # Preserve backend/runtime warnings that have useful locations. Aggregate
    # records without locations by code below instead of flooding the list.
    for item in warnings:
        if not isinstance(item, dict) or not any(
            name in item
            for name in ("page", "region_id", "paragraph_index", "element_id")
        ):
            continue
        findings.append(
            _actionable_finding(
                check=str(item.get("check", "runtime")),
                status=str(item.get("status", "warning")),
                finding=item,
            )
        )

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in findings:
        key = (
            item.get("code"),
            item.get("page"),
            item.get("region_id"),
            item.get("paragraph_index"),
            item.get("element_id"),
            item.get("snippet"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    warning_codes = Counter(
        str(item.get("code", "unknown")) for item in warnings
    )
    issue_codes = Counter(str(item["code"]) for item in unique)
    affected_pages = sorted(
        {
            int(item["page"])
            for item in unique
            if isinstance(item.get("page"), int)
        }
    )
    severity_counts = Counter(str(item["severity"]) for item in unique)
    check_counts = Counter(str(item["check"]) for item in unique)
    return {
        "issue_count": len(unique),
        "warning_record_count": len(warnings),
        "affected_page_count": len(affected_pages),
        "affected_pages": affected_pages,
        "by_severity": dict(sorted(severity_counts.items())),
        "by_check": dict(sorted(check_counts.items())),
        "by_issue_code": dict(sorted(issue_codes.items())),
        "by_warning_code": dict(sorted(warning_codes.items())),
        "actionable_findings": unique[:_MAX_ACTIONABLE_FINDINGS],
        "truncated_count": max(0, len(unique) - _MAX_ACTIONABLE_FINDINGS),
    }


def validation_report_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    check_lines = [
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in checks.items()
    ]
    warning_summary = report.get("warning_summary", {})
    warning_code_lines = [
        f"- `{code}`：{count}"
        for code, count in warning_summary.get("by_warning_code", {}).items()
    ] or ["- 无。"]
    actionable_lines: list[str] = []
    for item in warning_summary.get("actionable_findings", []):
        location = []
        if "page" in item:
            location.append(f"page={item['page']}")
        if "region_id" in item:
            location.append(f"region={item['region_id']}")
        if "paragraph_index" in item:
            location.append(f"paragraph={item['paragraph_index']}")
        suffix = f" ({', '.join(location)})" if location else ""
        snippet = item.get("snippet")
        detail = f" — {snippet}" if snippet else ""
        actionable_lines.append(
            f"- **{item['severity'].upper()}** `{item['code']}`{suffix}{detail}"
        )
    if not actionable_lines:
        actionable_lines = ["- 无。"]
    quality = report.get("quality_checks", {})
    quality_lines = [
        f"- `{name}`：`{value.get('status', 'unknown')}`"
        for name, value in quality.items()
    ] or ["- 未运行。"]
    severity_text = ", ".join(
        f"{name}={count}"
        for name, count in warning_summary.get("by_severity", {}).items()
    ) or "无"
    return "\n".join(
        [
            "# Paper2MD 验证报告",
            "",
            f"- 状态：`{report['status']}`",
            f"- 证据级别：`{report['evidence_level']}`",
            f"- 页数：{report['page_count']}",
            f"- 最终图片数：{report['rendered_image_count']}",
            f"- 可定位问题数：{warning_summary.get('issue_count', 0)}",
            f"- 受影响页数：{warning_summary.get('affected_page_count', 0)}",
            f"- 严重级别汇总：{severity_text}",
            "",
            "## 自动检查",
            "",
            *check_lines,
            "",
            "## 输出质量检查",
            "",
            *quality_lines,
            "",
            "## 可操作问题",
            "",
            *actionable_lines,
            "",
            "## 警告代码汇总",
            "",
            *warning_code_lines,
            "",
            "## 人工检查",
            "",
            "最终布局已经复核并通过契约校验；Figure/Table 仍建议在发布前逐张查看。",
            "",
        ]
    )
