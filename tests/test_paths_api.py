import tempfile
import unittest
from pathlib import Path

from paperwright.api import PaperWright
from paperwright.backends.base import BackendCapabilities, BackendIdentity, BackendRegistry
from paperwright.backends.pdfbox import PDFBoxBackend
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.exceptions import BackendUnavailableError, PathSafetyError
from paperwright.paths import validate_conversion_paths

from helpers import minimal_document


class FakeBackend:
    identity = BackendIdentity("fixture", "1", "fixture", None)
    capabilities = BackendCapabilities(True, False, False, False, False)

    def extract(self, source, config):
        return minimal_document()


class PathAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "input.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\\n% self-generated path fixture\\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_paths_are_resolved(self):
        source, output = validate_conversion_paths(
            self.pdf,
            self.root / "new-output",
            PaperWrightConfig(workspace_root=self.root),
        )
        self.assertEqual(source, self.pdf.resolve())
        self.assertEqual(output, (self.root / "new-output").resolve())

    def test_existing_output_is_rejected(self):
        output = self.root / "exists"
        output.mkdir()
        with self.assertRaisesRegex(PathSafetyError, "拒绝覆盖"):
            validate_conversion_paths(self.pdf, output, PaperWrightConfig())

    def test_workspace_escape_is_rejected(self):
        with self.assertRaisesRegex(PathSafetyError, "越出"):
            validate_conversion_paths(
                self.pdf,
                self.root.parent / "outside",
                PaperWrightConfig(workspace_root=self.root),
            )

    def test_input_inside_output_is_rejected(self):
        with self.assertRaisesRegex(PathSafetyError, "包含输入"):
            validate_conversion_paths(self.pdf, self.root, PaperWrightConfig())

    def test_missing_input_is_rejected(self):
        with self.assertRaisesRegex(PathSafetyError, "不存在"):
            validate_conversion_paths(
                self.root / "missing.pdf",
                self.root / "out",
                PaperWrightConfig(),
            )

    def test_backend_registry_rejects_duplicate(self):
        registry = BackendRegistry()
        registry.register("fixture", FakeBackend())
        with self.assertRaises(ValueError):
            registry.register("fixture", FakeBackend())

    def test_api_uses_injected_backend(self):
        config = PaperWrightConfig(backend="pdfium", workspace_root=self.root)
        registry = BackendRegistry()
        fake = FakeBackend()
        registry.register("pdfium", fake)
        product = PaperWright(config=config, registry=registry)
        document = product.extract_physical_document(self.pdf, self.root / "out")
        self.assertEqual(document.backend, "fixture")
        self.assertEqual(document.pages[0].elements[0].text, "Café α bootstrap")

    def test_pdfium_runtime_identity_is_locked(self):
        backend = PDFiumBackend()
        self.assertEqual(backend.identity.wrapper_version, "5.11.0")
        self.assertEqual(backend.identity.engine_version, "151.0.7920.0")
        self.assertEqual(len(backend.identity.binary_sha256 or ""), 64)

    def test_pdfbox_stub_fails_explicitly(self):
        with self.assertRaises(BackendUnavailableError):
            PDFBoxBackend().extract(self.pdf, PaperWrightConfig(backend="pdfbox"))


if __name__ == "__main__":
    unittest.main()
