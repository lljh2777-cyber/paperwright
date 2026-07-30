"""Typed bootstrap configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Paper2MDConfig":
        allowed = {
            "backend",
            "contract_version",
            "limits",
            "output",
            "region_render",
            "workspace_root",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ConfigurationError(
                f"配置包含未知字段: {', '.join(unknown)}"
            )
        limits_value = value.get("limits", {})
        output_value = value.get("output", {})
        region_value = value.get("region_render", {})
        for name, item, fields in (
            (
                "limits",
                limits_value,
                {"max_pages", "max_output_bytes", "timeout_seconds"},
            ),
            (
                "output",
                output_value,
                {"allow_existing_directory", "atomic_write"},
            ),
            (
                "region_render",
                region_value,
                {
                    "enabled",
                    "page_indices",
                    "mode",
                    "max_candidates_per_document",
                },
            ),
        ):
            if not isinstance(item, dict):
                raise ConfigurationError(f"{name} 必须是 JSON object")
            extra = sorted(set(item) - fields)
            if extra:
                raise ConfigurationError(
                    f"{name} 包含未知字段: {', '.join(extra)}"
                )
        workspace = value.get("workspace_root")
        config = cls(
            backend=value.get("backend", "pdfium"),
            contract_version=value.get(
                "contract_version", "paper2md-physical-document-v0.2"
            ),
            limits=Limits(**limits_value),
            output=OutputPolicy(**output_value),
            region_render=RegionRenderPolicy(
                enabled=region_value.get("enabled", False),
                page_indices=tuple(region_value.get("page_indices", ())),
                mode=region_value.get("mode", "off"),
                max_candidates_per_document=region_value.get(
                    "max_candidates_per_document", 12
                ),
            ),
            workspace_root=Path(workspace) if workspace is not None else None,
        )
        config.validate()
        if config.output.allow_existing_directory:
            raise ConfigurationError(
                "Alpha 不允许 allow_existing_directory=true"
            )
        if not config.output.atomic_write:
            raise ConfigurationError("Alpha 要求 atomic_write=true")
        return config


def load_config(path: str | Path | None) -> Paper2MDConfig:
    """Load strict JSON configuration; defaults apply when path is omitted."""

    if path is None:
        return Paper2MDConfig()
    config_path = Path(path).expanduser()
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"无法读取配置 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("配置根节点必须是 JSON object")
    return Paper2MDConfig.from_dict(value)


def with_cli_overrides(
    base: Paper2MDConfig,
    *,
    backend: str | None = None,
    workspace_root: Path | None = None,
    region_mode: str | None = None,
    region_pages: tuple[int, ...] | None = None,
    region_max_candidates: int | None = None,
) -> Paper2MDConfig:
    """Apply only explicitly supplied CLI values over a loaded config."""

    current_region = base.region_render
    mode = region_mode if region_mode is not None else current_region.effective_mode
    pages = (
        region_pages
        if region_pages is not None
        else current_region.page_indices
    )
    if mode != "explicit" and region_pages is None:
        pages = ()
    result = Paper2MDConfig(
        backend=backend if backend is not None else base.backend,
        contract_version=base.contract_version,
        limits=base.limits,
        output=base.output,
        region_render=RegionRenderPolicy(
            mode=mode,
            page_indices=pages,
            max_candidates_per_document=(
                region_max_candidates
                if region_max_candidates is not None
                else current_region.max_candidates_per_document
            ),
        ),
        workspace_root=(
            workspace_root
            if workspace_root is not None
            else base.workspace_root
        ),
    )
    result.validate()
    return result
