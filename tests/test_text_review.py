from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from paper2md.article_model import canonical_article_model_json
from paper2md.cli import main
from paper2md.exceptions import ContractValidationError
from paper2md.text_review import (
    TEXT_REVIEW_CONTRACT_VERSION,
    TEXT_TASK_CONTRACT_VERSION,
    apply_text_review,
    build_text_task,
    canonical_text_review_json,
    canonical_text_task_json,
    text_task_sha256,
    validate_text_review,
    validate_text_task,
)
from tests import test_reader


class TextReviewContractTests(unittest.TestCase):
    def _model(self):
        compilation, reader, _, _, _ = test_reader.ReaderContractTests()._compile()
        return compilation.article_model(
            source_sha256=reader["source_sha256"]
        )

    @staticmethod
    def _review(task, operations):
        return {
            "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
            "task_sha256": text_task_sha256(task),
            "source_sha256": task["source_sha256"],
            "article_model_sha256": task["article_model"]["sha256"],
            "reviewer": "fixture-text-agent",
            "operations": operations,
        }

    @staticmethod
    def _operation(block, markdown, *, mode="format-only"):
        return {
            "op": "replace-markdown",
            "block_id": block["id"],
            "expected_markdown_sha256": block["markdown_sha256"],
            "change_mode": mode,
            "markdown": markdown,
            "reason": "Fixture cleanup with no semantic rewrite.",
        }

    def test_task_is_text_only_deterministic_and_pinned_to_model(self):
        model = self._model()
        first = build_text_task(model)
        second = build_text_task(model)
        self.assertEqual(first["contract_version"], TEXT_TASK_CONTRACT_VERSION)
        self.assertEqual(canonical_text_task_json(first), canonical_text_task_json(second))
        self.assertNotIn("source_spans", first["blocks"][1])
        self.assertNotIn("assets", first)
        slot = next(item for item in first["blocks"] if item["kind"] == "visual_slot")
        self.assertFalse(slot["editable"])
        validate_text_task(first, article_model=model)

        changed_model = deepcopy(model)
        changed_model["blocks"][1]["markdown"] += " changed"
        with self.assertRaisesRegex(ContractValidationError, "hash"):
            validate_text_task(first, article_model=changed_model)

    def test_format_only_review_preserves_identity_and_visible_text(self):
        model = self._model()
        task = build_text_task(model)
        block = next(item for item in task["blocks"] if item["kind"] == "body")
        review = self._review(
            task,
            [self._operation(block, f"*{block['markdown']}*")],
        )
        validate_text_review(review, task=task)
        self.assertEqual(
            canonical_text_review_json(review, task=task),
            canonical_text_review_json(deepcopy(review), task=task),
        )
        result = apply_text_review(model, task=task, review=review)
        before = {item["id"]: item for item in model["blocks"]}
        after = {item["id"]: item for item in result["blocks"]}
        self.assertEqual(set(before), set(after))
        self.assertEqual(after[block["id"]]["markdown"], f"*{block['markdown']}*")
        self.assertEqual(model["blocks"][1]["markdown"], block["markdown"])
        for block_id in before:
            self.assertEqual(before[block_id]["source_spans"], after[block_id]["source_spans"])

    def test_dehyphenation_allows_only_exact_line_break_cleanup(self):
        model = self._model()
        body = next(item for item in model["blocks"] if item["kind"] == "body")
        body["markdown"] = "A multi- modal method."
        task = build_text_task(model)
        task_body = next(item for item in task["blocks"] if item["id"] == body["id"])
        valid = self._review(
            task,
            [self._operation(task_body, "A multimodal method.", mode="dehyphenation")],
        )
        validate_text_review(valid, task=task)

        semantic = deepcopy(valid)
        semantic["operations"][0]["markdown"] = "A generative method."
        with self.assertRaisesRegex(ContractValidationError, "只允许"):
            validate_text_review(semantic, task=task)

    def test_review_rejects_visual_slots_stale_hashes_and_semantic_rewrites(self):
        model = self._model()
        task = build_text_task(model)
        slot = next(item for item in task["blocks"] if item["kind"] == "visual_slot")
        visual = self._review(
            task,
            [self._operation(slot, "![changed](images/changed.png)")],
        )
        with self.assertRaisesRegex(ContractValidationError, "视觉槽位"):
            validate_text_review(visual, task=task)

        body = next(item for item in task["blocks"] if item["kind"] == "body")
        semantic = self._review(
            task,
            [self._operation(body, "Invented scientific conclusion.")],
        )
        with self.assertRaisesRegex(ContractValidationError, "可见文本"):
            validate_text_review(semantic, task=task)

        heading_injection = self._review(
            task,
            [self._operation(body, f"## {body['markdown']}")],
        )
        with self.assertRaisesRegex(ContractValidationError, "标题层级"):
            validate_text_review(heading_injection, task=task)

        stale = self._review(task, [self._operation(body, f"*{body['markdown']}*")])
        stale["operations"][0]["expected_markdown_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "markdown hash"):
            validate_text_review(stale, task=task)

    def test_cli_prepare_validate_and_apply_refuses_overwrite(self):
        model = self._model()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "article-model.json"
            task_path = root / "text-task.json"
            review_path = root / "text-review.json"
            output_path = root / "article-model.reviewed.json"
            model_path.write_text(
                canonical_article_model_json(model), encoding="utf-8", newline="\n"
            )

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["text-prepare", str(model_path), str(task_path)]), 0)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["status"], "prepared")
            task = json.loads(task_path.read_text(encoding="utf-8"))

            body = next(item for item in task["blocks"] if item["kind"] == "body")
            review = self._review(task, [self._operation(body, f"*{body['markdown']}*")])
            review_path.write_text(
                canonical_text_review_json(review, task=task),
                encoding="utf-8",
                newline="\n",
            )
            self.assertEqual(
                main(["validate-text-task", str(task_path), "--article-model", str(model_path)]),
                0,
            )
            self.assertEqual(
                main(["validate-text-review", str(review_path), "--task", str(task_path)]),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "text-apply",
                        str(model_path),
                        str(task_path),
                        str(review_path),
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertTrue(output_path.is_file())
            self.assertNotEqual(
                main(
                    [
                        "text-apply",
                        str(model_path),
                        str(task_path),
                        str(review_path),
                        str(output_path),
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
