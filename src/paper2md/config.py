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
    """Opt-in policy for conservative clipped page-region rendering.

    ``enabled`` is retained as a compatibility alias for the Phase 4 spike:
    ``enabled=True`` with page indices means ``mode="explicit"``.  New callers
    should set ``mode`` directly.
    """

    enabled: bool = False
    page_indices: tuple[int, ...] = ()
    mode: str = "off"
    max_candidates_per_document: int = 12

    @property
    def effective_mode(self) -> str:
        return "explicit" if self.enabled else self.mode

    def validate(self) -> None:
        if self.mode not in {"off", "explicit", "auto"}:
            raise ConfigurationError(
                "region_render mode 必须是 off/explicit/auto"
            )
        if self.enabled and self.mode not in {"off", "explicit"}:
            raise ConfigurationError(
                "legacy enabled 不能与 auto mode 同时使用"
            )
        if any(not isinstance(item, int) or item < 0 for item in self.page_indices):
            raise ConfigurationError("region_render page_indices 必须是非负整数")
        if len(set(self.page_indices)) != len(self.page_indices):
            raise ConfigurationError("region_render page_indices 不得重复")
        if self.effective_mode == "explicit" and not self.page_indices:
            raise ConfigurationError(
                "region_render explicit mode 必须明确限定页面"
            )
        if self.effective_mode in {"off", "auto"} and self.page_indices:
            raise ConfigurationError(
                "region_render page_indices 仅允许用于 explicit mode"
            )
        if (
            not isinstance(self.max_candidates_per_document, int)
            or not 1 <= self.max_candidates_per_document <= 100
        ):
            raise ConfigurationError(
                "region_render max_candidates_per_document 必须位于 [1,100]"
            )


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
