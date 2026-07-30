"""Planned PDFBox comparison/fallback boundary; no JAR is bundled."""

from __future__ import annotations

from pathlib import Path

from ..config import Paper2MDConfig
from ..exceptions import BackendUnavailableError
from ..models import PhysicalDocument
from .base import BackendCapabilities, BackendIdentity


class PDFBoxBackend:
    identity = BackendIdentity(
        name="pdfbox",
        wrapper_version="not-installed",
        engine_version=None,
        binary_sha256=None,
    )
    capabilities = BackendCapabilities(
        text_runs=False,
        images=False,
        vectors=False,
        links=False,
        render=False,
    )

    def extract(self, source: Path, config: Paper2MDConfig) -> PhysicalDocument:
        raise BackendUnavailableError(
            "PDFBox 仅作为对照/回退接口；bootstrap 不下载或运行 JAR"
        )
