"""Replaceable backend protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import PaperWrightConfig
from ..exceptions import BackendUnavailableError
from ..models import PhysicalDocument


@dataclass(frozen=True)
class BackendIdentity:
    name: str
    wrapper_version: str
    engine_version: str | None
    binary_sha256: str | None


@dataclass(frozen=True)
class BackendCapabilities:
    text_runs: bool
    images: bool
    vectors: bool
    links: bool
    render: bool


@dataclass(frozen=True)
class ExtractedAsset:
    """In-memory asset emitted by a backend before atomic output assembly."""

    element_id: str
    suggested_name: str
    media_type: str
    data: bytes
    width_px: int
    height_px: int


@dataclass(frozen=True)
class BackendResult:
    document: PhysicalDocument
    assets: tuple[ExtractedAsset, ...] = ()
    warnings: tuple[dict[str, object], ...] = ()
    performance: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    identity: BackendIdentity
    capabilities: BackendCapabilities

    def extract(
        self, source: Path, config: PaperWrightConfig
    ) -> PhysicalDocument | BackendResult:
        """Map one PDF to the backend-neutral PhysicalDocument."""


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, Backend] = {}

    def register(self, name: str, backend: Backend) -> None:
        if not name or name in self._backends:
            raise ValueError(f"后端名称为空或已注册: {name}")
        self._backends[name] = backend

    def get(self, name: str) -> Backend:
        try:
            return self._backends[name]
        except KeyError as exc:
            raise BackendUnavailableError(
                f"后端 {name!r} 在 bootstrap 中不可用；未下载或捆绑运行时"
            ) from exc
