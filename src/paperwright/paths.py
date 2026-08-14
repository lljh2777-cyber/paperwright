"""Input/output path safety rules."""

from __future__ import annotations

from pathlib import Path

from .config import PaperWrightConfig
from .exceptions import OutputConflictError, PathSafetyError, UnsupportedInputError


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_input_pdf(input_pdf: str | Path) -> Path:
    """Resolve and validate a read-only PDF input."""

    source = Path(input_pdf).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise PathSafetyError(f"输入 PDF 不存在或不是文件: {source}")
    if source.suffix.lower() != ".pdf":
        raise UnsupportedInputError("输入文件扩展名必须是 .pdf")
    return source


def validate_conversion_paths(
    input_pdf: str | Path,
    output_dir: str | Path,
    config: PaperWrightConfig,
) -> tuple[Path, Path]:
    source = validate_input_pdf(input_pdf)
    destination = Path(output_dir).expanduser().resolve()
    if destination == source:
        raise OutputConflictError("输出目录不能与输入文件冲突")
    if destination in source.parents:
        raise OutputConflictError("输出目录不能包含输入 PDF")
    if config.workspace_root is not None:
        root = config.workspace_root.expanduser().resolve()
        if not _is_relative_to(destination, root):
            raise PathSafetyError("输出目录越出 workspace_root")
    if destination.exists() and not config.output.allow_existing_directory:
        raise OutputConflictError(f"输出目录已存在，拒绝覆盖: {destination}")
    return source, destination
