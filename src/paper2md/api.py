"""Minimal Python API facade."""

from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from .backends.base import Backend, BackendRegistry, BackendResult
from .config import Paper2MDConfig
from .exceptions import BackendExecutionError
from .layout_candidates import generate_layout_tasks, propose_content_rois
from .layout_export import export_layout_task_bundle
from .layout_models import FinalLayout, LayoutTask
from .layout_review import validate_layout_review
from .layout_roi import (
    canonical_content_roi_json,
    content_roi_contract,
    content_roi_review_instructions,
    load_confirmed_content_rois,
)
from .layout_writer import write_layout_outputs
from .models import PhysicalDocument
from .paths import validate_conversion_paths, validate_input_pdf
from .raster_layout import analyze_page_raster
from .writer import write_outputs


@dataclass(frozen=True)
class ConversionResult:
    output_dir: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class LayoutPreparationResult:
    output_dir: Path
    index: dict[str, Any]


@dataclass(frozen=True)
class ExtractionBenchmarkResult:
    source_sha256: str
    page_count: int
    backend: str
    performance: dict[str, Any]


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

    def benchmark_extraction(
        self,
        input_pdf: str | Path,
        *,
        mode: str = "full",
    ) -> ExtractionBenchmarkResult:
        """Run one read-only extraction and return non-deterministic timings."""

        if mode not in {"full", "text-only", "raster"}:
            raise ValueError("benchmark mode must be full, text-only, or raster")
        pipeline_started = time.perf_counter_ns()
        source = validate_input_pdf(input_pdf)
        backend = self.registry.get(self.config.backend)
        extractor = backend.extract
        if mode in {"text-only", "raster"}:
            extractor = getattr(backend, "extract_text_only", None)
            if not callable(extractor):
                raise BackendExecutionError(
                    f"{backend.identity.name} 后端不支持 text-only 基准"
                )
        extracted = extractor(source, self.config)
        result = (
            extracted
            if isinstance(extracted, BackendResult)
            else BackendResult(extracted)
        )
        if result.document.backend != backend.identity.name:
            raise BackendExecutionError("后端输出身份与注册身份不一致")
        if not result.performance:
            raise BackendExecutionError(
                f"{backend.identity.name} 后端没有提供性能计时"
            )
        performance = dict(result.performance)
        if mode == "raster":
            render_previews = getattr(backend, "render_page_previews", None)
            if not callable(render_previews):
                raise BackendExecutionError(
                    f"{backend.identity.name} 后端不支持批量页面预览"
                )
            render_started = time.perf_counter_ns()
            previews = render_previews(
                source,
                tuple(range(len(result.document.pages))),
                scale=1.5,
            )
            render_ms = round(
                (time.perf_counter_ns() - render_started) / 1_000_000,
                3,
            )
            raster_pages: list[dict[str, Any]] = []
            raster_analyses: dict[int, Any] = {}
            analysis_started = time.perf_counter_ns()
            for page, preview in zip(
                result.document.pages,
                previews,
                strict=True,
            ):
                page_started = time.perf_counter_ns()
                raster = analyze_page_raster(preview, page)
                raster_analyses[page.page_index] = raster.analysis
                record = raster.analysis.to_dict()
                record["analysis_ms"] = round(
                    (time.perf_counter_ns() - page_started) / 1_000_000,
                    3,
                )
                raster_pages.append(record)
            analysis_ms = round(
                (time.perf_counter_ns() - analysis_started) / 1_000_000,
                3,
            )
            layout_started = time.perf_counter_ns()
            content_rois = propose_content_rois(
                result.document,
                raster_analyses=raster_analyses,
            )
            tasks = generate_layout_tasks(
                result.document,
                content_rois=content_rois,
                content_roi_source="raster_rule_proposed",
                raster_analyses=raster_analyses,
            )
            layout_ms = round(
                (time.perf_counter_ns() - layout_started) / 1_000_000,
                3,
            )
            performance["raster_layout"] = {
                "schema_version": "paper2md-raster-benchmark-v0.1",
                "preview_scale": 1.5,
                "render_total_ms": render_ms,
                "analysis_total_ms": analysis_ms,
                "layout_task_total_ms": layout_ms,
                "pages": raster_pages,
                "layout_tasks": [
                    {
                        "page_index": task.page.page_index,
                        "contract_version": task.contract_version,
                        "candidate_count": len(task.candidates),
                        "raster_candidate_count": sum(
                            "raster" in candidate.element_kinds
                            for candidate in task.candidates
                        ),
                        "analysis_roi": task.metadata["analysis_roi"]["bbox"],
                        "raster_suppressed_element_count": len(
                            task.metadata.get(
                                "raster_suppressed_element_ids",
                                [],
                            )
                        ),
                        "candidates": [
                            {
                                "candidate_id": candidate.candidate_id,
                                "bbox": candidate.bbox.to_dict(),
                                "element_kinds": list(candidate.element_kinds),
                                "raster_region_count": candidate.features.get(
                                    "raster_region_count",
                                    0,
                                ),
                            }
                            for candidate in task.candidates
                        ],
                    }
                    for task in tasks
                ],
            }
        performance["pipeline_total_ms"] = round(
            (time.perf_counter_ns() - pipeline_started) / 1_000_000,
            3,
        )
        return ExtractionBenchmarkResult(
            source_sha256=result.document.source_sha256,
            page_count=len(result.document.pages),
            backend=result.document.backend,
            performance=performance,
        )

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
        content_roi_json: str | Path | None = None,
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
            if content_roi_json is None:
                content_rois = propose_content_rois(result.document)
                roi_source = "rule_proposed"
                roi_contract = content_roi_contract(
                    result.document,
                    content_rois,
                )
            else:
                content_rois, roi_source = load_confirmed_content_rois(
                    content_roi_json,
                    result.document,
                )
                roi_contract = content_roi_contract(
                    result.document,
                    content_rois,
                    review_status="confirmed",
                    reviewer=roi_source.removeprefix("confirmed:"),
                )
            tasks = generate_layout_tasks(
                result.document,
                content_rois=content_rois,
                content_roi_source=roi_source,
            )
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
            (temporary / "content-roi.json").write_text(
                canonical_content_roi_json(roi_contract),
                encoding="utf-8",
                newline="\n",
            )
            (temporary / "content-roi-instructions.md").write_text(
                content_roi_review_instructions(),
                encoding="utf-8",
                newline="\n",
            )
            index = {
                "contract_version": "paper2md-layout-review-index-v0.1",
                "source_sha256": result.document.source_sha256,
                "backend": result.document.backend,
                "backend_version": result.document.backend_version,
                "preview_scale": preview_scale,
                "page_count": len(tasks),
                "content_roi": {
                    "path": "content-roi.json",
                    "review_status": roi_contract["review_status"],
                    "source": roi_source,
                    "destructive_crop": False,
                },
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
        references_mode: str = "keep",
        evidence_level: str = "standard",
        include_source_pdf: bool = False,
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
            content_rois, roi_source = load_confirmed_content_rois(
                review_root / "content-roi.json",
                result.document,
            )
            regenerated = generate_layout_tasks(
                result.document,
                content_rois=content_rois,
                content_roi_source=roi_source,
            )
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
                references_mode=references_mode,
                evidence_level=evidence_level,
                include_source_pdf=include_source_pdf,
                review_root=review_root,
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
