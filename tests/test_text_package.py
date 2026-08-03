from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from paper2md.article_model import (
    article_model_to_reader,
    canonical_article_model_json,
    render_article_markdown,
    validate_article_model,
)
from paper2md.cli import main
from paper2md.exceptions import ContractValidationError, OutputConflictError
from paper2md.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    TEXT_REVIEWED_MANIFEST_VERSION,
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    sha256_file,
    validate_manifest,
)
from paper2md.reader import canonical_reader_json
from paper2md.text_package import (
    build_text_reviewed_package,
    validate_text_reviewed_package,
)
from paper2md.text_review import (
    TEXT_REVIEW_CONTRACT_VERSION,
    build_text_task,
    canonical_text_review_json,
    canonical_text_task_json,
    text_task_sha256,
)
from tests import test_reader


class TextPackageTests(unittest.TestCase):
    def _source_package(self, root: Path):
        compilation, reader, _, _, image_data = (
            test_reader.ReaderContractTests()._compile()
        )
        model = compilation.article_model(
            source_sha256=reader["source_sha256"]
        )
        (root / "images").mkdir(parents=True)
        (root / "_paper2md").mkdir()
        article_path = root / "article.md"
        model_path = root / "_paper2md/article-model.json"
        reader_path = root / "_paper2md/reader.json"
        image_path = root / "images/figure-0001.png"
        article_path.write_text(
            render_article_markdown(model), encoding="utf-8", newline="\n"
        )
        model_path.write_text(
            canonical_article_model_json(model), encoding="utf-8", newline="\n"
        )
        reader_path.write_text(
            canonical_reader_json(article_model_to_reader(model)),
            encoding="utf-8",
            newline="\n",
        )
        image_path.write_bytes(image_data)
        outputs = [
            OutputFile("article.md", "markdown", article_path.stat().st_size, sha256_file(article_path)),
            OutputFile(
                "_paper2md/article-model.json",
                "article_model",
                model_path.stat().st_size,
                sha256_file(model_path),
            ),
            OutputFile(
                "_paper2md/reader.json",
                "reader_index",
                reader_path.stat().st_size,
                sha256_file(reader_path),
            ),
            OutputFile(
                "images/figure-0001.png",
                "visual_region",
                image_path.stat().st_size,
                sha256_file(image_path),
            ),
        ]
        manifest = build_manifest(
            source_sha256=model["source_sha256"],
            backend="fixture",
            backend_version="1",
            contract_version="paper2md-physical-document-v0.2",
            page_count=1,
            status="success",
            outputs=outputs,
            manifest_version=HYBRID_LAYOUT_MANIFEST_VERSION,
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
            reader={
                "contract_version": reader["contract_version"],
                "path": "_paper2md/reader.json",
                "sha256": sha256_file(reader_path),
                "article_path": "article.md",
                "article_sha256": reader["article"]["sha256"],
                "anchor_contract": reader["article"]["anchor_contract"],
            },
            article_model={
                "contract_version": model["contract_version"],
                "path": "_paper2md/article-model.json",
                "sha256": sha256_file(model_path),
            },
        )
        (root / "_paper2md/manifest.json").write_text(
            canonical_manifest_json(manifest), encoding="utf-8", newline="\n"
        )
        return model, image_data

    @staticmethod
    def _review(task):
        block = next(item for item in task["blocks"] if item["kind"] == "body")
        return {
            "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
            "task_sha256": text_task_sha256(task),
            "source_sha256": task["source_sha256"],
            "article_model_sha256": task["article_model"]["sha256"],
            "reviewer": "fixture-text-agent",
            "operations": [
                {
                    "op": "replace-markdown",
                    "block_id": block["id"],
                    "expected_markdown_sha256": block["markdown_sha256"],
                    "change_mode": "format-only",
                    "markdown": f"*{block['markdown']}*",
                    "reason": "Preserve visible text while adding emphasis.",
                }
            ],
        }

    @staticmethod
    def _tree_hashes(root: Path):
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_builds_deterministic_atomic_v010_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model, image_data = self._source_package(source)
            task = build_text_task(model)
            review = self._review(task)
            first = root / "first"
            second = root / "second"

            result = build_text_reviewed_package(source, task, review, first)
            build_text_reviewed_package(source, task, review, second)
            self.assertEqual(result.manifest["manifest_version"], TEXT_REVIEWED_MANIFEST_VERSION)
            self.assertEqual(self._tree_hashes(first), self._tree_hashes(second))
            self.assertEqual(
                (first / "images/figure-0001.png").read_bytes(), image_data
            )
            manifest = json.loads(
                (first / "_paper2md/manifest.json").read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            validate_text_reviewed_package(first)
            for output in manifest["outputs"]:
                path = first.joinpath(*Path(output["path"]).parts)
                self.assertEqual(path.stat().st_size, output["size_bytes"])
                self.assertEqual(sha256_file(path), output["sha256"])

            reviewed_model = json.loads(
                (first / "_paper2md/article-model.json").read_text(encoding="utf-8")
            )
            validate_article_model(reviewed_model, root=first)
            self.assertEqual(
                (first / "article.md").read_text(encoding="utf-8"),
                render_article_markdown(reviewed_model),
            )
            before = {item["id"]: item for item in model["blocks"]}
            after = {item["id"]: item for item in reviewed_model["blocks"]}
            self.assertEqual(set(before), set(after))
            for block_id in before:
                self.assertEqual(
                    before[block_id]["source_spans"], after[block_id]["source_spans"]
                )
            self.assertTrue(
                (first / "_paper2md/06-text-review/validation-report.md").is_file()
            )
            self.assertEqual(
                json.loads(
                    (source / "_paper2md/manifest.json").read_text(encoding="utf-8")
                )["manifest_version"],
                HYBRID_LAYOUT_MANIFEST_VERSION,
            )

    def test_rejects_tampered_source_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model, _ = self._source_package(source)
            task = build_text_task(model)
            review = self._review(task)
            (source / "images/figure-0001.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(ContractValidationError, "哈希"):
                build_text_reviewed_package(source, task, review, root / "output")

            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(OutputConflictError):
                build_text_reviewed_package(source, task, review, existing)

    def test_cli_writes_complete_package_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model, _ = self._source_package(source)
            task = build_text_task(model)
            review = self._review(task)
            task_path = root / "text-task.json"
            review_path = root / "text-review.json"
            task_path.write_text(
                canonical_text_task_json(task), encoding="utf-8", newline="\n"
            )
            review_path.write_text(
                canonical_text_review_json(review, task=task),
                encoding="utf-8",
                newline="\n",
            )
            output_dir = root / "output"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(
                    [
                        "text-package",
                        str(source),
                        str(task_path),
                        str(review_path),
                        str(output_dir),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["manifest_version"],
                TEXT_REVIEWED_MANIFEST_VERSION,
            )
            validate_stdout = io.StringIO()
            with redirect_stdout(validate_stdout):
                self.assertEqual(
                    main(["validate-text-package", str(output_dir)]),
                    0,
                )
            self.assertEqual(json.loads(validate_stdout.getvalue())["status"], "valid")
            self.assertNotEqual(
                main(
                    [
                        "text-package",
                        str(source),
                        str(task_path),
                        str(review_path),
                        str(output_dir),
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
