"""Minimal Python API facade."""

from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

from .backends.base import Backend, BackendRegistry, BackendResult
from .config import Paper2MDConfig
from .exceptions import BackendExecutionError
from .layout_candidates import generate_layout_tasks
from .layout_export import export_layout_task_bundle
from .layout_models import FinalLayout, LayoutTask
from .layout_review import validate_layout_review
from .layout_writer import write_layout_outputs
from .models import PhysicalDocument
from .paths import validate_conversion_paths
from .writer import write_outputs


@dataclass(frozen=True)
class ConversionResult:
    output_dir: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class LayoutPreparationResult:
    output_dir: Path
    index: dict[str, Any]


class Paper2MD:
    def __init__(
        self,
        config: Paper2MDConfig | None = None,
        registry: BackendRegistry | None = None,
    ) -> None:
        self.config = config or Paper2MDConfig()
        self.config.validate()
        self.registry = registry or BackendRegistry()

    def register_backend(self, name: str, backend: Backend) -> None:
        self.registry.register(name, backend)

    def extract_physical_document(
        self,
        input_pdf: str | Path,
        output_dir: str | Path,
    ) -> PhysicalDocument:
        source, _ = validate_conversion_paths(input_pdf, output_dir, self.config)
        backend = self.registry.get(self.config.backend)
        result = backend.extract(source, self.config)
        document = result.document if isinstance(result, BackendResult) else result
        if document.backend != backend.identity.name:
            raise ValueError("后端输出身份与注册身份不一致")
        return document

    def convert(
        self,
        input_pdf: str | Path,
        output_dir: str | Path,
    ) -> ConversionResult:
        source, destination = validate_conversion_paths(
            input_pdf, output_dir, self.config
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        backend = self.registry.get(self.config.backend)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.paper2md-",
                dir=destination.parent,
            )
        )
        try:
            extracted = backend.extract(source, self.config)
            result = (
                extracted
                if isinstance(extracted, BackendResult)
                else BackendResult(extracted)
            )
            if result.document.backend != backend.identity.name:
                raise BackendExecutionError("后端输出身份与注册身份不一致")
            prepared = write_outputs(
                root=temporary,
                document=result.document,
                assets=result.assets,
                backend_warnings=result.warnings,
                source=source,
                region_renderer=(
                    backend if callable(getattr(backend, "render_region", None)) else None
                ),
                region_render_page_indices=(
                    frozenset(self.config.region_render.page_indices)
                    if self.config.region_render.effective_mode == "explicit"
                    else frozenset()
                ),
                region_render_mode=self.config.region_render.effective_mode,
                region_render_max_candidates=(
                    self.config.region_render.max_candidates_per_document
                ),
            )
            total = sum(
                path.stat().st_size
                for path in temporary.rglob("*")
                if path.is_file()
            )
            if total > self.config.limits.max_output_bytes:
                raise BackendExecutionError(
                    f"输出 {total} bytes 超过限制 "
                    f"{self.config.limits.max_output_bytes}"
                )
            if destination.exists():
                raise BackendExecutionError("原子提交前发现输出目录已存在")
            os.replace(temporary, destination)
            return ConversionResult(destination, prepared.manifest)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def prepare_layout_review(
        self,
        input_pdf: str | Path,
        output_dir: str | Path,
        *,
        preview_scale: float = 1.5,
    ) -> LayoutPreparationResult:
        """Export page review bundles without changing conversion output."""

        source, destination = validate_conversion_paths(
            input_pdf,
            output_dir,
            self.config,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        backend = self.registry.get(self.config.backend)
        render_preview = getattr(backend, "render_page_preview", None)
        if not callable(render_preview):
            raise BackendExecutionError(
                f"{self.config.backend} 后端不支持 layout preview"
            )
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.paper2md-layout-",
                dir=destination.parent,
            )
        )
        try:
            extracted = backend.extract(source, self.config)
            result = (
                extracted
                if isinstance(extracted, BackendResult)
                else BackendResult(extracted)
            )
            tasks = generate_layout_tasks(result.document)
            pages: list[dict[str, Any]] = []
            for task in tasks:
                preview = render_preview(
                    source,
                    task.page.page_index,
                    scale=preview_scale,
                )
                page_dir = f"page-{task.page.page_index + 1:04d}"
                export_layout_task_bundle(
                    temporary / page_dir,
                    task,
                    preview,
                )
                pages.append(
                    {
                        "page_index": task.page.page_index,
                        "directory": page_dir,
                        "task_sha256": task.deterministic_sha256(),
                        "candidate_count": len(task.candidates),
                        "separator_count": len(task.separators),
                    }
                )
            index = {
                "contract_version": "paper2md-layout-review-index-v0.1",
                "source_sha256": result.document.source_sha256,
                "backend": result.document.backend,
                "backend_version": result.document.backend_version,
                "preview_scale": preview_scale,
                "page_count": len(tasks),
                "pages": pages,
                "ocr_used": False,
            }
            (temporary / "review-index.json").write_text(
                json.dumps(
                    index,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            total = sum(
                path.stat().st_size
                for path in temporary.rglob("*")
                if path.is_file()
            )
            if total > self.config.limits.max_output_bytes:
                raise BackendExecutionError(
                    f"布局审查输出 {total} bytes 超过限制 "
                    f"{self.config.limits.max_output_bytes}"
                )
            if destination.exists():
                raise BackendExecutionError("布局审查原子提交前发现输出目录已存在")
            os.replace(temporary, destination)
            return LayoutPreparationResult(destination, index)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def apply_layout_review(
        self,
        input_pdf: str | Path,
        review_dir: str | Path,
        output_dir: str | Path,
        *,
        visual_scale: float = 2.0,
    ) -> ConversionResult:
        """Apply reviewed page layouts to a new, atomic conversion output."""

        source, destination = validate_conversion_paths(
            input_pdf,
            output_dir,
            self.config,
        )
        review_root = Path(review_dir).expanduser().resolve()
        if not review_root.is_dir():
            raise BackendExecutionError(
                f"布局审查目录不存在或不是目录: {review_root}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        backend = self.registry.get(self.config.backend)
        if not callable(getattr(backend, "render_region", None)):
            raise BackendExecutionError(
                f"{self.config.backend} 后端不支持布局区域渲染"
            )
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.paper2md-layout-apply-",
                dir=destination.parent,
            )
        )
        try:
            extracted = backend.extract(source, self.config)
            result = (
                extracted
                if isinstance(extracted, BackendResult)
                else BackendResult(extracted)
            )
            regenerated = generate_layout_tasks(result.document)
            reviews: list[FinalLayout] = []
            for expected_task in regenerated:
                page_root = (
                    review_root
                    / f"page-{expected_task.page.page_index + 1:04d}"
                )
                task_path = page_root / "layout-task.json"
                review_path = page_root / "final-layout.json"
                if not task_path.is_file() or not review_path.is_file():
                    raise BackendExecutionError(
                        f"页面布局审查文件不完整: {page_root}"
                    )
                recorded_task = LayoutTask.from_dict(
                    json.loads(task_path.read_text(encoding="utf-8"))
                )
                if recorded_task.canonical_json() != expected_task.canonical_json():
                    raise BackendExecutionError(
                        f"page {expected_task.page.page_index} "
                        "布局任务与当前 PDF/候选算法不一致"
                    )
                review = FinalLayout.from_dict(
                    json.loads(review_path.read_text(encoding="utf-8"))
                )
                validate_layout_review(review, recorded_task)
                reviews.append(review)
            prepared = write_layout_outputs(
                root=temporary,
                source=source,
                document=result.document,
                assets=result.assets,
                backend_warnings=result.warnings,
                tasks=regenerated,
                layouts=tuple(reviews),
                region_renderer=backend,
                visual_scale=visual_scale,
            )
            total = sum(
                path.stat().st_size
                for path in temporary.rglob("*")
                if path.is_file()
            )
            if total > self.config.limits.max_output_bytes:
                raise BackendExecutionError(
                    f"布局转换输出 {total} bytes 超过限制 "
                    f"{self.config.limits.max_output_bytes}"
                )
            if destination.exists():
                raise BackendExecutionError("布局转换原子提交前发现输出目录已存在")
            os.replace(temporary, destination)
            return ConversionResult(destination, prepared.manifest)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
