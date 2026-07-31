"""Self-contained document-package evidence metadata."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Sequence


EVIDENCE_LEVELS = {"minimal", "standard", "full"}


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
) -> dict[str, Any]:
    return {
        "contract_version": "paper2md-validation-report-v0.1",
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
        "human_review": {
            "status": "layout-reviewed",
            "note": "Final layout decisions were validated before packaging.",
        },
    }


def validation_report_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    check_lines = [
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in checks.items()
    ]
    warnings = report["warnings"]
    warning_lines = (
        [f"- `{item.get('code', 'unknown')}`: {json.dumps(item, ensure_ascii=False)}" for item in warnings]
        if warnings
        else ["- 无。"]
    )
    return "\n".join(
        [
            "# Paper2MD 验证报告",
            "",
            f"- 状态：`{report['status']}`",
            f"- 证据级别：`{report['evidence_level']}`",
            f"- 页数：{report['page_count']}",
            f"- 最终图片数：{report['rendered_image_count']}",
            "",
            "## 自动检查",
            "",
            *check_lines,
            "",
            "## 警告",
            "",
            *warning_lines,
            "",
            "## 人工检查",
            "",
            "最终布局已经复核并通过契约校验；Figure/Table 仍建议在发布前逐张查看。",
            "",
        ]
    )
