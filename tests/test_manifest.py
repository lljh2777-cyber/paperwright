import json
import unittest
from pathlib import Path

from paper2md.exceptions import ContractValidationError
from paper2md.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    OutputFile,
    PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION,
    READER_HYBRID_LAYOUT_MANIFEST_VERSION,
    TEXT_REVIEWED_MANIFEST_VERSION,
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
        for name in (
            "manifest.schema.json",
            "physical_document.schema.json",
            "article_model.schema.json",
            "reader.schema.json",
            "text_task.schema.json",
            "text_review.schema.json",
        ):
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

    def hybrid_manifest(self, *, version=HYBRID_LAYOUT_MANIFEST_VERSION):
        article_hash = "a" * 64
        reader_hash = "b" * 64
        article_model_hash = "e" * 64
        reader_versions = {
            READER_HYBRID_LAYOUT_MANIFEST_VERSION,
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }
        article_model_versions = {
            HYBRID_LAYOUT_MANIFEST_VERSION,
            TEXT_REVIEWED_MANIFEST_VERSION,
        }
        text_hashes = {
            "task": "1" * 64,
            "review": "2" * 64,
            "validation": "3" * 64,
        }
        return build_manifest(
            source_sha256="c" * 64,
            backend="fixture",
            backend_version="1",
            contract_version="paper2md-physical-document-v0.2",
            page_count=1,
            status="success",
            outputs=[
                OutputFile("article.md", "markdown", 12, article_hash),
                OutputFile(
                    "_paper2md/reader.json",
                    "reader_index",
                    24,
                    reader_hash,
                ),
                *(
                    [
                        OutputFile(
                            "_paper2md/article-model.json",
                            "article_model",
                            48,
                            article_model_hash,
                        )
                    ]
                    if version in article_model_versions
                    else []
                ),
                *(
                    [
                        OutputFile(
                            "_paper2md/06-text-review/text-task.json",
                            "text_task",
                            30,
                            text_hashes["task"],
                        ),
                        OutputFile(
                            "_paper2md/06-text-review/text-review.json",
                            "text_review",
                            30,
                            text_hashes["review"],
                        ),
                        OutputFile(
                            "_paper2md/06-text-review/validation-report.json",
                            "text_validation_report",
                            30,
                            text_hashes["validation"],
                        ),
                    ]
                    if version == TEXT_REVIEWED_MANIFEST_VERSION
                    else []
                ),
            ],
            manifest_version=version,
            layout_review={
                "mode": "hybrid-reviewed",
                "prompt_version": "fixture-v1",
                "candidate_generator_version": "fixture-v1",
                "feature_schema_version": "fixture-v1",
                "evidence_level": "minimal",
                "provenance_path": None,
                "provenance_sha256": None,
                "ocr_used": False,
                "pages": [],
            },
            reader=(
                {
                    "contract_version": "paper2md-reader-v0.1",
                    "path": "_paper2md/reader.json",
                    "sha256": reader_hash,
                    "article_path": "article.md",
                    "article_sha256": article_hash,
                    "anchor_contract": "paper2md-markdown-anchor-v0.1",
                }
                if version in reader_versions
                else None
            ),
            article_model=(
                {
                    "contract_version": "paper2md-article-model-v0.1",
                    "path": "_paper2md/article-model.json",
                    "sha256": article_model_hash,
                }
                if version in article_model_versions
                else None
            ),
            text_review=(
                {
                    "task_contract_version": "paper2md-text-task-v0.1",
                    "review_contract_version": "paper2md-text-review-v0.1",
                    "task_path": "_paper2md/06-text-review/text-task.json",
                    "task_sha256": text_hashes["task"],
                    "review_path": "_paper2md/06-text-review/text-review.json",
                    "review_sha256": text_hashes["review"],
                    "source_article_model_sha256": article_model_hash,
                    "parent_manifest_sha256": "4" * 64,
                    "reviewer": "fixture-text-agent",
                    "operation_count": 1,
                    "validation_path": "_paper2md/06-text-review/validation-report.json",
                    "validation_sha256": text_hashes["validation"],
                }
                if version == TEXT_REVIEWED_MANIFEST_VERSION
                else None
            ),
        )

    def test_hybrid_v09_requires_reader_and_article_model_matching_outputs(self):
        value = self.hybrid_manifest()
        validate_manifest(value)
        missing = dict(value)
        missing.pop("reader")
        with self.assertRaisesRegex(ContractValidationError, "缺少 reader"):
            validate_manifest(missing)
        value["reader"]["article_sha256"] = "d" * 64
        with self.assertRaisesRegex(ContractValidationError, "outputs"):
            validate_manifest(value)

        value = self.hybrid_manifest()
        value["article_model"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ContractValidationError, "outputs"):
            validate_manifest(value)

    def test_hybrid_v08_remains_accepted_with_reader_without_article_model(self):
        value = self.hybrid_manifest(
            version=READER_HYBRID_LAYOUT_MANIFEST_VERSION
        )
        validate_manifest(value)

    def test_text_reviewed_v010_requires_hash_matched_provenance(self):
        value = self.hybrid_manifest(version=TEXT_REVIEWED_MANIFEST_VERSION)
        validate_manifest(value)

        missing = dict(value)
        missing.pop("text_review")
        with self.assertRaisesRegex(ContractValidationError, "text_review"):
            validate_manifest(missing)

        changed = self.hybrid_manifest(version=TEXT_REVIEWED_MANIFEST_VERSION)
        changed["text_review"]["review_sha256"] = "9" * 64
        with self.assertRaisesRegex(ContractValidationError, "outputs"):
            validate_manifest(changed)

    def test_hybrid_v07_remains_accepted_without_reader(self):
        value = self.hybrid_manifest(
            version=PREVIOUS_HYBRID_LAYOUT_MANIFEST_VERSION
        )
        value["outputs"] = [
            item for item in value["outputs"] if item["role"] != "reader_index"
        ]
        validate_manifest(value)


if __name__ == "__main__":
    unittest.main()
