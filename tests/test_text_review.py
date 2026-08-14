from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from paperwright.article_model import canonical_article_model_json
from paperwright.cli import main
from paperwright.exceptions import ContractValidationError
from paperwright.text_review import (
    TEXT_REVIEW_CONTRACT_VERSION,
    TEXT_TASK_CONTRACT_VERSION,
    _join_joiner,
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


class JoinBlocksContractTests(unittest.TestCase):
    """text-review v0.2 join-blocks: model-agnostic same-paragraph joins."""

    SHA = "0" * 64

    @classmethod
    def _span(cls, page_index: int, *, y: float = 0.1, tag: str = "0") -> dict:
        return {
            "page_index": page_index,
            "bbox": {"x": 0.1, "y": y, "width": 0.4, "height": 0.02},
            "region_id": None,
            "paragraph_index": 0,
            "elements_sha256": hashlib.sha256(
                (cls.SHA + tag).encode("utf-8")
            ).hexdigest(),
        }

    def _model(self, *, joinable: bool = True) -> dict:
        from paperwright.article_model import build_article_model
        from paperwright.reader_contract import stable_reader_id

        def block_id(kind: str, span: dict) -> str:
            return stable_reader_id(
                "blk", self.SHA, {"kind": kind, "source_spans": [span]}
            )

        kinds = ("title", "body", "body", "body")
        if joinable:
            markdowns = (
                "# Fixture",
                "The results are shown",
                "in the table below. More detail",
                "follows here.",
            )
        else:
            markdowns = (
                "# Fixture",
                "The results are shown",
                "Follows here with a capital.",
                "in the table below.",
            )
        spans = [
            self._span(0, y=0.05 + 0.1 * order, tag=str(order))
            for order in range(1, len(kinds) + 1)
        ]
        blocks = []
        markdown_by_id = {}
        for order, (kind, span, markdown) in enumerate(
            zip(kinds, spans, markdowns), start=1
        ):
            block_id_value = block_id(kind, span)
            blocks.append(
                {
                    "id": block_id_value,
                    "kind": kind,
                    "order": order,
                    "asset_id": None,
                    "source_spans": [span],
                }
            )
            markdown_by_id[block_id_value] = markdown
        return build_article_model(
            source_sha256=self.SHA,
            blocks=blocks,
            markdown_by_id=markdown_by_id,
            assets=[],
            relations=[],
        )

    @staticmethod
    def _join_review(task, first, second):
        return {
            "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
            "task_sha256": text_task_sha256(task),
            "source_sha256": task["source_sha256"],
            "article_model_sha256": task["article_model"]["sha256"],
            "reviewer": "fixture-text-agent",
            "operations": [
                {
                    "op": "join-blocks",
                    "target_block_ids": [first["id"], second["id"]],
                    "reason": "Same paragraph split at a column boundary.",
                }
            ],
        }

    def test_join_blocks_merges_two_adjacent_body_blocks(self):
        model = self._model()
        task = build_text_task(model)
        self.assertEqual(task["contract_version"], TEXT_TASK_CONTRACT_VERSION)
        self.assertIn("join-blocks", task["policy"]["allowed_operations"])
        first = task["blocks"][1]
        second = task["blocks"][2]
        self.assertTrue(first["editable"])
        self.assertEqual(first["page"], second["page"])
        self.assertEqual(abs(first["order"] - second["order"]), 1)
        self.assertIs(first["in_relations"], False)

        review = self._join_review(task, first, second)
        validate_text_review(review, task=task)
        result = apply_text_review(model, task=task, review=review)
        after = {item["id"]: item for item in result["blocks"]}
        self.assertIn(first["id"], after)
        self.assertNotIn(second["id"], after)
        self.assertEqual(
            after[first["id"]]["markdown"],
            "The results are shown in the table below. More detail",
        )
        # The head keeps its stable identity; only its markdown grows.
        self.assertEqual(len(after[first["id"]]["source_spans"]), 1)
        self.assertEqual(
            [item["order"] for item in result["blocks"]],
            [1, 2, 3],
        )

    def test_join_joiner_handles_slash_boundary(self):
        self.assertEqual(_join_joiner("https://github.com/"), "")
        self.assertEqual(_join_joiner("dehyphen-"), "")
        self.assertEqual(_join_joiner("non-breaking hyphen\u2011"), "")
        self.assertEqual(_join_joiner("a normal end"), " ")

    def test_join_blocks_rejects_violations(self):
        model = self._model()
        task = build_text_task(model)
        first = task["blocks"][1]
        second = task["blocks"][2]

        def review_with(operation):
            return {
                "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
                "task_sha256": text_task_sha256(task),
                "source_sha256": task["source_sha256"],
                "article_model_sha256": task["article_model"]["sha256"],
                "reviewer": "fixture-text-agent",
                "operations": [operation],
            }

        # non-adjacent order (second block is order 4, first is 2)
        fourth = model["blocks"][3]
        with self.assertRaisesRegex(ContractValidationError, "相邻"):
            validate_text_review(
                review_with(
                    {
                        "op": "join-blocks",
                        "target_block_ids": [first["id"], fourth["id"]],
                        "reason": "r",
                    }
                ),
                task=task,
            )
        # continuation must start lowercase
        cap_model = self._model(joinable=False)
        cap_task = build_text_task(cap_model)
        with self.assertRaisesRegex(ContractValidationError, "小写"):
            validate_text_review(
                {
                    "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
                    "task_sha256": text_task_sha256(cap_task),
                    "source_sha256": cap_task["source_sha256"],
                    "article_model_sha256": cap_task["article_model"]["sha256"],
                    "reviewer": "fixture-text-agent",
                    "operations": [
                        {
                            "op": "join-blocks",
                            "target_block_ids": [
                                cap_model["blocks"][1]["id"],
                                cap_model["blocks"][2]["id"],
                            ],
                            "reason": "r",
                        }
                    ],
                },
                task=cap_task,
            )
        # block referenced by a relation is not joinable
        from paperwright.article_model import ARTICLE_MODEL_CONTRACT_VERSION
        from paperwright.reader_contract import normalized_visible_text
        import hashlib as _hashlib

        def _sha(value: str) -> str:
            return _hashlib.sha256(value.encode("utf-8")).hexdigest()

        def _task_with_in_relations(block_ids, in_relation_ids):
            blocks = []
            for index, block_id in enumerate(block_ids, start=1):
                blocks.append(
                    {
                        "id": block_id,
                        "kind": "body",
                        "order": index,
                        "page": 0,
                        "markdown": "sample text line",
                        "markdown_sha256": _sha("sample text line"),
                        "visible_text_sha256": _sha(
                            normalized_visible_text("sample text line")
                        ),
                        "editable": True,
                        "in_relations": block_id in in_relation_ids,
                    }
                )
            return {
                "contract_version": TEXT_TASK_CONTRACT_VERSION,
                "source_sha256": self.SHA,
                "article_model": {
                    "contract_version": ARTICLE_MODEL_CONTRACT_VERSION,
                    "sha256": self.SHA,
                },
                "policy": {
                    "text_source": "born-digital-native-pdf",
                    "allowed_operations": ["replace-markdown", "join-blocks"],
                    "allowed_change_modes": ["format-only", "dehyphenation"],
                    "immutable_fields": [
                        "id",
                        "kind",
                        "order",
                        "source_spans",
                        "asset_id",
                        "assets",
                        "relations",
                    ],
                    "text_equivalence_version": "paperwright-text-equivalence-v0.1",
                },
                "blocks": blocks,
            }

        first_id = "blk_" + "1" * 24
        second_id = "blk_" + "2" * 24
        rel_task = _task_with_in_relations(
            [first_id, second_id], {second_id}
        )
        validate_text_task(rel_task)
        with self.assertRaisesRegex(ContractValidationError, "关系"):
            validate_text_review(
                {
                    "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
                    "task_sha256": text_task_sha256(rel_task),
                    "source_sha256": rel_task["source_sha256"],
                    "article_model_sha256": rel_task["article_model"]["sha256"],
                    "reviewer": "fixture-text-agent",
                    "operations": [
                        {
                            "op": "join-blocks",
                            "target_block_ids": [first_id, second_id],
                            "reason": "r",
                        }
                    ],
                },
                task=rel_task,
            )

    def test_v1_task_does_not_allow_join_blocks(self):
        model = self._model()
        task = build_text_task(model)
        task_v1 = deepcopy(task)
        task_v1["contract_version"] = "paperwright-text-task-v0.1"
        task_v1["policy"]["allowed_operations"] = ["replace-markdown"]
        self.assertEqual(
            text_task_sha256(task_v1),
            text_task_sha256(task_v1),
        )
        validate_text_task(task_v1)
        blocks = {item["id"]: item for item in task["blocks"]}
        review = {
            "contract_version": "paperwright-text-review-v0.1",
            "task_sha256": text_task_sha256(task_v1),
            "source_sha256": task_v1["source_sha256"],
            "article_model_sha256": task_v1["article_model"]["sha256"],
            "reviewer": "fixture-text-agent",
            "operations": [
                {
                    "op": "join-blocks",
                    "target_block_ids": [
                        model["blocks"][1]["id"],
                        model["blocks"][2]["id"],
                    ],
                    "reason": "r",
                }
            ],
        }
        with self.assertRaisesRegex(ContractValidationError, "不受支持"):
            validate_text_review(review, task=task_v1)
