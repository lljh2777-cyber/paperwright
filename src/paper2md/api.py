"""Minimal Python API facade."""

from __future__ import annotations

from pathlib import Path

from .backends.base import Backend, BackendRegistry
from .config import Paper2MDConfig
from .models import PhysicalDocument
from .paths import validate_conversion_paths


class Paper2MD:
    def __init__(
        self,
        config: Paper2MDConfig | None = None,
        registry: BackendRegistry | None = None,
    ) -> None:
        self.config = config or Paper2MDConfig()
        self.config.validate()
        self.registry = registry or BackendRegistry()

    def register_backend(self, name: str, backend: Backend) -> None:
        self.registry.register(name, backend)

    def extract_physical_document(
        self,
        input_pdf: str | Path,
        output_dir: str | Path,
    ) -> PhysicalDocument:
        source, _ = validate_conversion_paths(input_pdf, output_dir, self.config)
        backend = self.registry.get(self.config.backend)
        document = backend.extract(source, self.config)
        if document.backend != backend.identity.name:
            raise ValueError("后端输出身份与注册身份不一致")
        return document
