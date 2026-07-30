"""Paper2MD Alpha command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import Paper2MD
from .backends.pdfbox import PDFBoxBackend
from .backends.pdfium import PDFiumBackend
from .batch import classify_error, collect_batch_inputs, run_batch
from .config import load_config, with_cli_overrides
from .exceptions import (
    BackendUnavailableError,
    ConfigurationError,
    Paper2MDError,
)
from .models import PhysicalDocument


def _add_runtime_options(
    parser: argparse.ArgumentParser,
    *,
    allow_explicit: bool,
) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help="严格 JSON 配置；优先级为 defaults < config < 显式 CLI 参数",
    )
    parser.add_argument(
        "--backend",
        choices=("pdfium", "pdfbox"),
        default=None,
        help="默认 pdfium；pdfbox 未绑定时明确失败",
    )
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument(
        "--region-render-mode",
        choices=("off", "explicit", "auto") if allow_explicit else ("off", "auto"),
        default=None,
        help="保守区域渲染；默认关闭",
    )
    if allow_explicit:
        parser.add_argument(
            "--region-render-page",
            type=int,
            action="append",
            default=None,
            metavar="ZERO_BASED_PAGE",
            help="explicit 模式限定的零基页索引，可重复",
        )
    parser.add_argument(
        "--region-render-max-candidates",
        type=int,
        default=None,
        help="auto 模式每文档候选硬上限",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper2md",
        description="本地、确定性、非 AI 的 born-digital 科研 PDF 重建 Alpha",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate-model", help="验证 PhysicalDocument JSON"
    )
    validate.add_argument("model_json", type=Path)

    convert = commands.add_parser("convert", help="转换单个 born-digital PDF")
    convert.add_argument("input_pdf", type=Path)
    convert.add_argument("output_dir", type=Path)
    _add_runtime_options(convert, allow_explicit=True)

    batch = commands.add_parser(
        "batch",
        help="非递归、确定性批量转换；每个 PDF 独立原子输出",
    )
    batch.add_argument("output_root", type=Path)
    source = batch.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input-dir",
        type=Path,
        help="只读取该目录第一层的 .pdf，不递归",
    )
    source.add_argument(
        "--input-file",
        type=Path,
        action="append",
        help="显式 PDF，可重复",
    )
    source.add_argument(
        "--file-list",
        type=Path,
        help="UTF-8 文本，每行一个 PDF；相对路径以清单目录为基准",
    )
    batch.add_argument(
        "--continue-on-error",
        action="store_true",
        help="单文档失败后继续其他输入；最终仍返回非零",
    )
    _add_runtime_options(batch, allow_explicit=False)
    return parser


def _validate_model(path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    document = PhysicalDocument.from_dict(value)
    print(
        json.dumps(
            {
                "status": "valid",
                "contract_version": document.contract_version,
                "page_count": len(document.pages),
                "deterministic_sha256": document.deterministic_sha256(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _configuration(args: argparse.Namespace, *, batch: bool):
    base = load_config(args.config)
    pages = getattr(args, "region_render_page", None)
    config = with_cli_overrides(
        base,
        backend=args.backend,
        workspace_root=args.workspace_root,
        region_mode=args.region_render_mode,
        region_pages=tuple(pages) if pages is not None else None,
        region_max_candidates=args.region_render_max_candidates,
    )
    if batch and config.region_render.effective_mode == "explicit":
        raise ConfigurationError("batch 只允许 region_render off 或 auto")
    return config


def _product(config) -> Paper2MD:
    product = Paper2MD(config=config)
    if config.backend == "pdfium":
        product.register_backend("pdfium", PDFiumBackend())
    else:
        product.register_backend("pdfbox", PDFBoxBackend())
    return product


def _convert(args: argparse.Namespace) -> int:
    config = _configuration(args, batch=False)
    result = _product(config).convert(args.input_pdf, args.output_dir)
    print(
        json.dumps(
            {
                "status": result.manifest["status"],
                "output_dir": str(result.output_dir),
                "page_count": result.manifest["page_count"],
                "backend": result.manifest["backend"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _batch(args: argparse.Namespace) -> int:
    config = _configuration(args, batch=True)
    if args.input_dir is not None:
        input_root = args.input_dir.expanduser().resolve()
        output_root = args.output_root.expanduser().resolve(strict=False)
        try:
            output_root.relative_to(input_root)
        except ValueError:
            pass
        else:
            raise ConfigurationError(
                "batch 输出根目录不能位于扫描输入目录内部"
            )
    inputs = collect_batch_inputs(
        input_dir=args.input_dir,
        input_files=tuple(args.input_file or ()),
        file_list=args.file_list,
    )
    result = run_batch(
        product=_product(config),
        config=config,
        inputs=inputs,
        output_root=args.output_root,
        tool_version=__version__,
        continue_on_error=args.continue_on_error,
    )
    print(
        json.dumps(
            {
                "status": result.summary["status"],
                "summary": "batch_summary.json",
                "counts": result.summary["counts"],
                "deterministic_content_sha256": result.summary[
                    "deterministic_content_sha256"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 3 if result.has_failures else 0


def _emit_error(exc: Exception, *, internal: bool = False) -> int:
    category = classify_error(exc)
    prefix = "内部错误" if internal else "输入或契约错误"
    if isinstance(exc, BackendUnavailableError):
        prefix = "后端不可用"
    print(
        f"{prefix} [{category}]: {exc}",
        file=sys.stderr,
    )
    if category == "backend_unavailable":
        return 4
    return 5 if internal else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "validate-model":
            return _validate_model(args.model_json)
        if args.command == "batch":
            return _batch(args)
        return _convert(args)
    except (
        Paper2MDError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        return _emit_error(exc)
    except Exception as exc:  # stable Alpha diagnostic boundary
        return _emit_error(exc, internal=True)


if __name__ == "__main__":
    raise SystemExit(main())
