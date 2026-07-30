"""Input/output path safety rules."""

from __future__ import annotations

from pathlib import Path

from .config import Paper2MDConfig
from .exceptions import PathSafetyError


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_conversion_paths(
    input_pdf: str | Path,
    output_dir: str | Path,
    config: Paper2MDConfig,
) -> tuple[Path, Path]:
    source = Path(input_pdf).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise PathSafetyError(f"输入 PDF 不存在或不是文件: {source}")
    if source.suffix.lower() != ".pdf":
        raise PathSafetyError("输入文件扩展名必须是 .pdf")
    if destination == source:
        raise PathSafetyError("输出目录不能与输入文件冲突")
    if destination in source.parents:
        raise PathSafetyError("输出目录不能包含输入 PDF")
    if config.workspace_root is not None:
        root = config.workspace_root.expanduser().resolve()
        if not _is_relative_to(destination, root):
            raise PathSafetyError("输出目录越出 workspace_root")
    if destination.exists() and not config.output.allow_existing_directory:
        raise PathSafetyError(f"输出目录已存在，拒绝覆盖: {destination}")
    return source, destination
