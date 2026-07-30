"""Replaceable backend protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import Paper2MDConfig
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


class Backend(Protocol):
    identity: BackendIdentity
    capabilities: BackendCapabilities

    def extract(self, source: Path, config: Paper2MDConfig) -> PhysicalDocument:
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
