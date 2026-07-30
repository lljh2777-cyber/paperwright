"""Typed bootstrap configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .exceptions import ConfigurationError


@dataclass(frozen=True)
class Limits:
    max_pages: int = 2000
    max_output_bytes: int = 512 * 1024 * 1024
    timeout_seconds: int = 120

    def validate(self) -> None:
        if self.max_pages <= 0:
            raise ConfigurationError("max_pages 必须大于 0")
        if self.max_output_bytes <= 0:
            raise ConfigurationError("max_output_bytes 必须大于 0")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds 必须大于 0")


@dataclass(frozen=True)
class OutputPolicy:
    allow_existing_directory: bool = False
    atomic_write: bool = True


@dataclass(frozen=True)
class RegionRenderPolicy:
    """Explicit opt-in for the bounded Phase 4 technical spike."""

    enabled: bool = False
    page_indices: tuple[int, ...] = ()

    def validate(self) -> None:
        if any(not isinstance(item, int) or item < 0 for item in self.page_indices):
            raise ConfigurationError("region_render page_indices 必须是非负整数")
        if len(set(self.page_indices)) != len(self.page_indices):
            raise ConfigurationError("region_render page_indices 不得重复")
        if self.enabled and not self.page_indices:
            raise ConfigurationError("启用 region_render 时必须明确限定页面")


@dataclass(frozen=True)
class Paper2MDConfig:
    backend: str = "pdfium"
    contract_version: str = "paper2md-physical-document-v0.2"
    limits: Limits = field(default_factory=Limits)
    output: OutputPolicy = field(default_factory=OutputPolicy)
    region_render: RegionRenderPolicy = field(default_factory=RegionRenderPolicy)
    workspace_root: Path | None = None

    def validate(self) -> None:
        if self.backend not in {"pdfium", "pdfbox"}:
            raise ConfigurationError(f"未知后端: {self.backend}")
        if self.contract_version != "paper2md-physical-document-v0.2":
            raise ConfigurationError("不支持的 PhysicalDocument 契约版本")
        self.limits.validate()
        self.region_render.validate()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["workspace_root"] = (
            str(self.workspace_root.resolve()) if self.workspace_root else None
        )
        return result
