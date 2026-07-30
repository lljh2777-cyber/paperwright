"""Paper2MD v2 MVP command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import Paper2MD
from .backends.pdfbox import PDFBoxBackend
from .backends.pdfium import PDFiumBackend
from .config import Paper2MDConfig, RegionRenderPolicy
from .exceptions import BackendUnavailableError, Paper2MDError
from .models import PhysicalDocument


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper2md")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-model", help="验证 PhysicalDocument JSON")
    validate.add_argument("model_json", type=Path)

    convert = commands.add_parser("convert", help="转换单个 born-digital PDF")
    convert.add_argument("input_pdf", type=Path)
    convert.add_argument("output_dir", type=Path)
    convert.add_argument("--backend", choices=("pdfium", "pdfbox"), default="pdfium")
    convert.add_argument("--workspace-root", type=Path)
    convert.add_argument(
        "--region-render-mode",
        choices=("off", "explicit", "auto"),
        default="off",
        help="保守区域渲染；默认关闭",
    )
    convert.add_argument(
        "--region-render-page",
        type=int,
        action="append",
        default=[],
        metavar="ZERO_BASED_PAGE",
        help="explicit 模式限定的零基页索引，可重复",
    )
    convert.add_argument(
        "--region-render-max-candidates",
        type=int,
        default=12,
        help="auto 模式每文档候选硬上限",
    )
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


def _convert(args: argparse.Namespace) -> int:
    config = Paper2MDConfig(
        backend=args.backend,
        workspace_root=args.workspace_root,
        region_render=RegionRenderPolicy(
            mode=args.region_render_mode,
            page_indices=tuple(args.region_render_page),
            max_candidates_per_document=args.region_render_max_candidates,
        ),
    )
    product = Paper2MD(config=config)
    if args.backend == "pdfium":
        product.register_backend("pdfium", PDFiumBackend())
    else:
        product.register_backend("pdfbox", PDFBoxBackend())
    result = product.convert(args.input_pdf, args.output_dir)
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "validate-model":
            return _validate_model(args.model_json)
        return _convert(args)
    except BackendUnavailableError as exc:
        print(f"后端不可用: {exc}", file=sys.stderr)
        return 4
    except (Paper2MDError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"输入或契约错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
