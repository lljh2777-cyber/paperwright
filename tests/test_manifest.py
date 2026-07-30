import json
import unittest
from pathlib import Path

from paper2md.exceptions import ContractValidationError
from paper2md.manifest import (
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    validate_manifest,
)


class ManifestTests(unittest.TestCase):
    def manifest(self):
        return build_manifest(
            source_sha256="c" * 64,
            backend="fixture",
            backend_version="1",
            contract_version="paper2md-physical-document-v0.2",
            page_count=1,
            status="success_with_degradation",
            outputs=[OutputFile("article.md", "markdown", 12, "d" * 64)],
            warnings=[{"code": "fixture", "message": "self-generated"}],
        )

    def test_schema_files_are_draft_2020_12_json(self):
        root = Path(__file__).parents[1] / "src/paper2md/schemas"
        for name in ("manifest.schema.json", "physical_document.schema.json"):
            value = json.loads((root / name).read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(value["additionalProperties"])

    def test_manifest_contract_accepts_valid(self):
        value = self.manifest()
        validate_manifest(value)
        self.assertEqual(value["outputs"][0]["path"], "article.md")

    def test_manifest_rejects_unknown_top_level_field(self):
        value = self.manifest()
        value["invented"] = True
        with self.assertRaisesRegex(ContractValidationError, "未知"):
            validate_manifest(value)

    def test_manifest_rejects_path_traversal(self):
        value = self.manifest()
        value["outputs"][0]["path"] = "../secret"
        with self.assertRaisesRegex(ContractValidationError, "路径穿越"):
            validate_manifest(value)

    def test_manifest_rejects_duplicate_output(self):
        value = self.manifest()
        value["outputs"].append(dict(value["outputs"][0]))
        with self.assertRaisesRegex(ContractValidationError, "重复"):
            validate_manifest(value)

    def test_manifest_json_is_deterministic(self):
        first = canonical_manifest_json(self.manifest())
        second = canonical_manifest_json(self.manifest())
        self.assertEqual(first.encode(), second.encode())


if __name__ == "__main__":
    unittest.main()
