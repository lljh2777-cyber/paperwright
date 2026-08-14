"""Conservative, deterministic batch orchestration for the source Alpha."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .api import PaperWright
from .config import PaperWrightConfig
from .exceptions import (
    BackendUnavailableError,
    ConfigurationError,
    CorruptInputError,
    OutputConflictError,
    PathSafetyError,
    UnsupportedInputError,
)

BATCH_SCHEMA_VERSION = "paperwright-batch-summary-v0.1"
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class BatchResult:
    output_root: Path
    summary: dict[str, Any]

    @property
    def has_failures(self) -> bool:
        return self.summary["counts"]["failed"] > 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: dict[str, Any]) -> str:
    deterministic = {
        key: item
        for key, item in value.items()
        if key not in {"runtime", "deterministic_content_sha256"}
    }
    payload = json.dumps(
        deterministic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise PathSafetyError(f"{label}不能是 symlink")


def collect_batch_inputs(
    *,
    input_dir: Path | None = None,
    input_files: Iterable[Path] = (),
    file_list: Path | None = None,
) -> tuple[Path, ...]:
    """Collect a bounded, non-recursive input set without following symlinks."""

    explicit_files = tuple(input_files)
    modes = sum(
        (
            input_dir is not None,
            bool(explicit_files),
            file_list is not None,
        )
    )
    if modes != 1:
        raise ConfigurationError(
            "batch 必须且只能指定 --input-dir、--input-file 或 --file-list"
        )
    candidates: list[Path]
    if input_dir is not None:
        _reject_symlink(input_dir, "输入目录")
        if not input_dir.exists() or not input_dir.is_dir():
            raise PathSafetyError("输入目录不存在或不是目录")
        # Intentionally one level only. Symlinked PDFs remain in the list and
        # are rejected per document instead of followed.
        candidates = [
            path
            for path in input_dir.iterdir()
            if path.suffix.casefold() == ".pdf"
            and (path.is_file() or path.is_symlink())
        ]
    elif file_list is not None:
        _reject_symlink(file_list, "文件清单")
        try:
            lines = file_list.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise PathSafetyError(f"无法读取文件清单: {exc}") from exc
        candidates = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            candidate = Path(stripped)
            if not candidate.is_absolute():
                candidate = file_list.parent / candidate
            candidates.append(candidate)
    else:
        candidates = list(explicit_files)
    if not candidates:
        raise ConfigurationError("batch 没有可处理的 PDF 输入")

    unique: dict[str, Path] = {}
    for path in candidates:
        absolute = path.expanduser().absolute()
        key = os.path.normcase(str(absolute))
        if key in unique:
            raise ConfigurationError(f"batch 输入重复: {path.name}")
        unique[key] = absolute
    return tuple(
        sorted(
            unique.values(),
            key=lambda path: (
                path.name.casefold(),
                path.name,
                os.path.normcase(str(path)),
            ),
        )
    )


def _safe_output_name(index: int, source: Path) -> str:
    stem = _SAFE_STEM.sub("-", source.stem).strip(".-_") or "document"
    return f"{index:04d}-{stem[:80]}"


def classify_error(exc: Exception) -> str:
    if isinstance(exc, CorruptInputError):
        return "corrupt"
    if isinstance(exc, UnsupportedInputError):
        return "unsupported"
    if isinstance(exc, BackendUnavailableError):
        return "backend_unavailable"
    if isinstance(exc, OutputConflictError):
        return "output_conflict"
    if isinstance(exc, PathSafetyError):
        return "path_safety"
    if isinstance(exc, ConfigurationError):
        return "configuration"
    return "internal"


def _safe_error_message(category: str) -> str:
    return {
        "corrupt": "PDF 无法由后端打开或结构损坏",
        "unsupported": "输入类型或功能不在 Alpha 支持范围",
        "backend_unavailable": "请求的后端在当前运行时不可用",
        "output_conflict": "输出路径已存在或与输入冲突",
        "path_safety": "路径违反安全边界",
        "configuration": "配置无效",
        "internal": "转换过程中发生未分类内部错误",
    }[category]


def _warning_classes(items: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(item.get("reason") or item.get("code") or "unspecified")
            for item in items
        }
    )


def _degraded_classes(items: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(
                item.get("reason")
                or item.get("kind")
                or item.get("type")
                or "unspecified"
            )
            for item in items
        }
    )


def validate_batch_summary(value: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "tool_version",
        "backend",
        "configuration",
        "status",
        "counts",
        "documents",
        "runtime",
        "deterministic_content_sha256",
    }
    if set(value) != required:
        raise ValueError("batch summary 顶层字段不完整或含未知字段")
    if value["schema_version"] != BATCH_SCHEMA_VERSION:
        raise ValueError("batch summary schema_version 不支持")
    documents = value["documents"]
    if not isinstance(documents, list):
        raise ValueError("batch summary documents 必须是 array")
    indexes = [item["input_index"] for item in documents]
    if indexes != list(range(1, len(documents) + 1)):
        raise ValueError("batch summary input_index 必须连续")
    allowed_status = {"success", "success_with_degradation", "failed", "not_run"}
    allowed_errors = {
        "corrupt",
        "unsupported",
        "backend_unavailable",
        "output_conflict",
        "path_safety",
        "configuration",
        "internal",
    }
    for item in documents:
        if item["status"] not in allowed_status:
            raise ValueError("batch summary document status 非法")
        output = item["output_dir"]
        if output is not None:
            output_path = Path(output)
            if output_path.is_absolute() or ".." in output_path.parts:
                raise ValueError("batch summary output_dir 必须是安全相对路径")
        error = item["error"]
        if item["status"] == "failed":
            if not error or error["category"] not in allowed_errors:
                raise ValueError("batch summary failed record 缺合法 error")
        elif error is not None:
            raise ValueError("非 failed record 不得包含 error")
        input_hash = item["input_sha256"]
        if input_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", input_hash):
            raise ValueError("batch summary input_sha256 非法")
    expected = _canonical_sha256(value)
    if value["deterministic_content_sha256"] != expected:
        raise ValueError("batch summary deterministic hash 不匹配")


def run_batch(
    *,
    product: PaperWright,
    config: PaperWrightConfig,
    inputs: tuple[Path, ...],
    output_root: Path,
    tool_version: str,
    continue_on_error: bool,
) -> BatchResult:
    destination = output_root.expanduser().absolute()
    if destination.exists():
        raise OutputConflictError("batch 输出根目录已存在，拒绝覆盖")
    if config.workspace_root is not None:
        workspace = config.workspace_root.expanduser().resolve()
        if not _relative_to(destination.resolve(strict=False), workspace):
            raise PathSafetyError("batch 输出根目录越出 workspace_root")
    for source in inputs:
        if _relative_to(source.absolute(), destination.resolve(strict=False)):
            raise OutputConflictError("batch 输出根目录不能包含输入 PDF")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    documents: list[dict[str, Any]] = []
    stopped = False
    for index, source in enumerate(inputs, start=1):
        output_name = _safe_output_name(index, source)
        base_record: dict[str, Any] = {
            "input_index": index,
            "input_name": source.name,
            "input_size_bytes": None,
            "input_sha256": None,
            "output_dir": output_name,
            "status": "not_run",
            "backend": None,
            "warnings": {"count": 0, "classes": []},
            "degraded": {"count": 0, "classes": []},
            "error": None,
            "reason": None,
        }
        if stopped:
            base_record["reason"] = "stopped_after_previous_error"
            documents.append(base_record)
            continue
        try:
            _reject_symlink(source, "输入 PDF")
            if not source.exists() or not source.is_file():
                raise PathSafetyError("输入 PDF 不存在或不是常规文件")
            if source.suffix.casefold() != ".pdf":
                raise UnsupportedInputError("输入文件扩展名必须是 .pdf")
            base_record["input_size_bytes"] = source.stat().st_size
            base_record["input_sha256"] = _sha256(source)
            result = product.convert(source, destination / output_name)
            manifest = result.manifest
            warnings = list(manifest.get("warnings", []))
            degraded = list(manifest.get("degraded", []))
            base_record["status"] = manifest["status"]
            base_record["backend"] = manifest["backend"]
            base_record["warnings"] = {
                "count": len(warnings),
                "classes": _warning_classes(warnings),
            }
            base_record["degraded"] = {
                "count": len(degraded),
                "classes": _degraded_classes(degraded),
            }
        except Exception as exc:  # per-document isolation boundary
            category = classify_error(exc)
            base_record["status"] = "failed"
            base_record["error"] = {
                "category": category,
                "message": _safe_error_message(category),
                "exception_type": type(exc).__name__,
            }
            if not continue_on_error:
                stopped = True
        documents.append(base_record)

    elapsed = time.monotonic() - started
    ended_at = datetime.now(timezone.utc)
    failed = sum(item["status"] == "failed" for item in documents)
    not_run = sum(item["status"] == "not_run" for item in documents)
    succeeded = len(documents) - failed - not_run
    summary: dict[str, Any] = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "tool_version": tool_version,
        "backend": config.backend,
        "configuration": {
            "region_render_mode": config.region_render.effective_mode,
            "region_render_max_candidates": (
                config.region_render.max_candidates_per_document
            ),
            "continue_on_error": continue_on_error,
            "input_order": "casefolded_filename_then_filename_then_path",
            "recursive_scan": False,
        },
        "status": (
            "completed"
            if failed == 0
            else "completed_with_errors"
            if continue_on_error
            else "stopped_on_error"
        ),
        "counts": {
            "total": len(documents),
            "succeeded": succeeded,
            "failed": failed,
            "not_run": not_run,
        },
        "documents": documents,
        "runtime": {
            "started_at_utc": started_at.isoformat().replace("+00:00", "Z"),
            "ended_at_utc": ended_at.isoformat().replace("+00:00", "Z"),
            "wall_seconds": elapsed,
            "excluded_from_deterministic_content_sha256": True,
        },
        "deterministic_content_sha256": "",
    }
    summary["deterministic_content_sha256"] = _canonical_sha256(summary)
    validate_batch_summary(summary)
    summary_path = destination / "batch_summary.json"
    temporary = destination / ".batch_summary.json.tmp"
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    return BatchResult(destination, summary)
