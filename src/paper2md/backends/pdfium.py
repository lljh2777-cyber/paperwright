"""Planned PDFium thin-adapter boundary; no runtime is bundled."""

from __future__ import annotations

from pathlib import Path

from ..config import Paper2MDConfig
from ..exceptions import BackendUnavailableError
from ..models import PhysicalDocument
from .base import BackendCapabilities, BackendIdentity


class PDFiumBackend:
    identity = BackendIdentity(
        name="pdfium",
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
            "PDFium 适配器仅定义接口；将在 v2-mvp 锁定版本与哈希后实现"
        )
