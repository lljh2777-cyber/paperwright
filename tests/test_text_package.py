from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from paperwright.article_model import (
    article_model_to_reader,
    canonical_article_model_json,
    render_article_markdown,
    validate_article_model,
)
from paperwright.article_tree import (
    article_tree_to_article_model,
    canonical_final_article_tree_json,
    validate_final_article_tree,
)
from paperwright.cli import main
from paperwright.exceptions import ContractValidationError, OutputConflictError
from paperwright.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    TEXT_REVIEWED_MANIFEST_VERSION,
    TEXT_SYNTHESIZED_MANIFEST_VERSION,
    OutputFile,
    build_manifest,
    canonical_manifest_json,
    sha256_file,
    validate_manifest,
)
from paperwright.reader import canonical_reader_json
from paperwright.synthesize import (
    SYNTHESIS_REVIEWER,
    SYNTHESIS_RUN_CONTRACT_VERSION,
    SynthesisError,
    build_synthesis_run,
    canonical_synthesis_run_json,
)
from paperwright.text_package import (
    build_text_reviewed_package,
    validate_text_reviewed_package,
)
from paperwright.text_review import (
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
        article_tree = compilation.article_tree(
            source_sha256=reader["source_sha256"]
        )
        (root / "images").mkdir(parents=True)
        (root / "_paperwright").mkdir()
        article_path = root / "article.md"
        model_path = root / "_paperwright/article-model.json"
        tree_path = root / "_paperwright/article-tree.json"
        reader_path = root / "_paperwright/reader.json"
        image_path = root / "images/figure-0001.png"
        article_path.write_text(
            render_article_markdown(model), encoding="utf-8", newline="\n"
        )
        model_path.write_text(
            canonical_article_model_json(model), encoding="utf-8", newline="\n"
        )
        tree_path.write_text(
            canonical_final_article_tree_json(article_tree),
            encoding="utf-8",
            newline="\n",
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
                "_paperwright/article-tree.json",
                "article_tree",
                tree_path.stat().st_size,
                sha256_file(tree_path),
            ),
            OutputFile(
                "_paperwright/article-model.json",
                "article_model",
                model_path.stat().st_size,
                sha256_file(model_path),
            ),
            OutputFile(
                "_paperwright/reader.json",
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
            contract_version="paperwright-physical-document-v0.2",
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
                "path": "_paperwright/reader.json",
                "sha256": sha256_file(reader_path),
                "article_path": "article.md",
                "article_sha256": reader["article"]["sha256"],
                "anchor_contract": reader["article"]["anchor_contract"],
            },
            article_model={
                "contract_version": model["contract_version"],
                "path": "_paperwright/article-model.json",
                "sha256": sha256_file(model_path),
            },
        )
        (root / "_paperwright/manifest.json").write_text(
            canonical_manifest_json(manifest), encoding="utf-8", newline="\n"
        )
        return model, image_data

    @staticmethod
    def _empty_synthesis_review(task):
        return {
            "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
            "task_sha256": text_task_sha256(task),
            "source_sha256": task["source_sha256"],
            "article_model_sha256": task["article_model"]["sha256"],
            "reviewer": SYNTHESIS_REVIEWER,
            "operations": [],
        }

    @staticmethod
    def _synthesis_run(task, review):
        script = "x = len(api.adjacent_body_pairs())\n"
        return build_synthesis_run(task, script, review)

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
                (first / "_paperwright/manifest.json").read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            validate_text_reviewed_package(first)
            for output in manifest["outputs"]:
                path = first.joinpath(*Path(output["path"]).parts)
                self.assertEqual(path.stat().st_size, output["size_bytes"])
                self.assertEqual(sha256_file(path), output["sha256"])

            reviewed_model = json.loads(
                (first / "_paperwright/article-model.json").read_text(encoding="utf-8")
            )
            reviewed_tree = json.loads(
                (first / "_paperwright/article-tree.json").read_text(
                    encoding="utf-8"
                )
            )
            validate_final_article_tree(reviewed_tree, root=first)
            self.assertEqual(
                reviewed_tree["structure_input"]["kind"],
                "text_review",
            )
            self.assertEqual(
                article_tree_to_article_model(reviewed_tree),
                reviewed_model,
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
                (first / "_paperwright/06-text-review/validation-report.md").is_file()
            )
            self.assertEqual(
                json.loads(
                    (source / "_paperwright/manifest.json").read_text(encoding="utf-8")
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

    def test_builds_v011_package_with_synthesis_run_replay_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model, _ = self._source_package(source)
            task = build_text_task(model)
            review = self._empty_synthesis_review(task)
            run = self._synthesis_run(task, review)
            run_path = root / "synthesize-run.json"
            run_path.write_text(
                canonical_synthesis_run_json(run),
                encoding="utf-8",
                newline="\n",
            )

            output = root / "output"
            result = build_text_reviewed_package(
                source, task, review, output, synthesis_run=run
            )
            self.assertEqual(
                result.manifest["manifest_version"],
                TEXT_SYNTHESIZED_MANIFEST_VERSION,
            )
            synthesis = result.manifest["synthesis_run"]
            self.assertEqual(
                synthesis["contract_version"], SYNTHESIS_RUN_CONTRACT_VERSION
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright/06-text-review/synthesize-run.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    output
                    / "_paperwright/06-text-review/source-article-model.json"
                ).is_file()
            )
            validate_text_reviewed_package(output)

            # Same inputs must replay the same output: a script whose emits
            # diverge from the persisted review is rejected before any
            # destination directory appears.
            tampered = dict(
                run,
                script=(
                    "pairs = api.blocks()\n"
                    "api.emit_join(pairs[0].id, pairs[1].id, 'tampered')\n"
                ),
            )
            bad_output = root / "bad-output"
            with self.assertRaisesRegex(SynthesisError, "重放"):
                build_text_reviewed_package(
                    source, task, review, bad_output, synthesis_run=tampered
                )
            self.assertFalse(bad_output.exists())

    def test_cli_text_package_with_synthesis_run_writes_v011(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model, _ = self._source_package(source)
            task = build_text_task(model)
            review = self._empty_synthesis_review(task)
            run = self._synthesis_run(task, review)
            task_path = root / "text-task.json"
            review_path = root / "text-review.json"
            run_path = root / "synthesize-run.json"
            task_path.write_text(
                canonical_text_task_json(task), encoding="utf-8", newline="\n"
            )
            review_path.write_text(
                canonical_text_review_json(review, task=task),
                encoding="utf-8",
                newline="\n",
            )
            run_path.write_text(
                canonical_synthesis_run_json(run),
                encoding="utf-8",
                newline="\n",
            )

            output = root / "output"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(
                    [
                        "text-package",
                        str(source),
                        str(task_path),
                        str(review_path),
                        str(output),
                        "--synthesis-run",
                        str(run_path),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(stdout.getvalue())["manifest_version"],
                TEXT_SYNTHESIZED_MANIFEST_VERSION,
            )
            validate_stdout = io.StringIO()
            with redirect_stdout(validate_stdout):
                self.assertEqual(
                    main(["validate-text-package", str(output)]), 0
                )
            self.assertEqual(
                json.loads(validate_stdout.getvalue())["status"], "valid"
            )


if __name__ == "__main__":
    unittest.main()
