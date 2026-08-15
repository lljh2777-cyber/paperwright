from __future__ import annotations

import json
from pathlib import Path
import runpy
import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.text_review import (
    apply_text_review,
    build_text_task,
    join_candidates,
    text_task_sha256,
    validate_text_review,
)
from paperwright.synthesize import (
    ConservationError,
    DSLValidationError,
    ReviewAPI,
    SYNTHESIS_REVIEWER,
    SYNTHESIS_RUN_CONTRACT_VERSION,
    SynthesisError,
    build_synthesis_review,
    build_synthesis_run,
    canonical_synthesis_run_json,
    enrich_task_blocks,
    execute_dsl,
    validate_synthesis_run,
    verify_join_conservation,
)
from tests import test_text_review


class SynthesisKernelTests(unittest.TestCase):
    """Deterministic L3 kernel: shared candidates, sandbox, conservation, L1 reuse."""

    def _model_task(self):
        model = test_text_review.JoinBlocksContractTests()._model()
        return model, build_text_task(model)

    def _enriched(self):
        model, task = self._model_task()
        return model, task, enrich_task_blocks(task, model)

    def test_join_candidates_respect_task_policy(self):
        from copy import deepcopy

        model, task = self._model_task()
        self.assertEqual(len(join_candidates(task)), 2)
        v1_task = deepcopy(task)
        v1_task["contract_version"] = "paperwright-text-task-v0.1"
        v1_task["policy"]["allowed_operations"] = ["replace-markdown"]
        self.assertEqual(join_candidates(v1_task), [])

        api = ReviewAPI(enrich_task_blocks(task, model), join_allowed=False)
        self.assertEqual(api.adjacent_body_pairs(), [])
        with self.assertRaises(ValueError):
            api.emit_join(task["blocks"][1]["id"], task["blocks"][2]["id"], "no")

    def test_join_candidates_are_exact_validator_preconditions(self):
        model, task = self._model_task()
        pairs = join_candidates(task)
        self.assertEqual(len(pairs), 2)

        block_by_id = {block["id"]: block for block in task["blocks"]}
        for previous, current in pairs:
            self.assertIn(previous["id"], block_by_id)
            self.assertIn(current["id"], block_by_id)
            review = {
                "contract_version": "paperwright-text-review-v0.2",
                "task_sha256": text_task_sha256(task),
                "source_sha256": task["source_sha256"],
                "article_model_sha256": task["article_model"]["sha256"],
                "reviewer": "fixture-text-agent",
                "operations": [
                    {
                        "op": "join-blocks",
                        "target_block_ids": [
                            previous["id"],
                            current["id"],
                        ],
                        "reason": "fixture join candidate",
                    }
                ],
            }
            validate_text_review(review, task=task)

        api = ReviewAPI(enrich_task_blocks(task, model))
        api_pairs = api.adjacent_body_pairs()
        self.assertEqual(
            [(previous["id"], current["id"]) for previous, current in api_pairs],
            [(previous["id"], current["id"]) for previous, current in pairs],
        )

    def test_dsl_join_emits_l1_compatible_review_and_applies(self):
        model, task, blocks = self._enriched()
        api = ReviewAPI(blocks)
        script = """
pairs = api.adjacent_body_pairs()
prev, curr = pairs[0]
if api.same_column(prev, curr) and api.vertical_gap(prev, curr) > 0:
    api.emit_join(prev.id, curr.id, "same column continuation")
"""
        operations = execute_dsl(script, api)
        self.assertEqual(len(operations), 1)

        review = build_synthesis_review(task, operations)
        self.assertEqual(review["reviewer"], SYNTHESIS_REVIEWER)
        validate_text_review(review, task=task)
        result = apply_text_review(model, task=task, review=review)
        after = {block["id"]: block["markdown"] for block in result["blocks"]}
        joined = operations[0]["target_block_ids"][0]
        self.assertIn(joined, after)
        self.assertEqual(
            after[joined],
            "The results are shown in the table below. More detail",
        )

    def test_execution_is_deterministic_and_task_is_not_mutated(self):
        model, task, blocks = self._enriched()
        task_before = json.dumps(task, ensure_ascii=False, sort_keys=True)
        script = """
pairs = api.adjacent_body_pairs()
prev, curr = pairs[0]
api.emit_join(prev.id, curr.id, "deterministic join")
"""
        first = execute_dsl(script, ReviewAPI(blocks))
        second = execute_dsl(script, ReviewAPI(blocks))
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(task, ensure_ascii=False, sort_keys=True),
            task_before,
        )

    def test_blocks_are_read_only_and_private_api_state_is_unreachable(self):
        _, _, blocks = self._enriched()
        mutate = """
pairs = api.adjacent_body_pairs()
pairs[0][0]["markdown"] = "tampered"
"""
        with self.assertRaises(TypeError):
            execute_dsl(mutate, ReviewAPI(blocks))

        assign_emitted = "api.emitted = []\n"
        with self.assertRaises(AttributeError):
            execute_dsl(assign_emitted, ReviewAPI(blocks))

        with self.assertRaises(DSLValidationError):
            execute_dsl("api._blocks\n", ReviewAPI(blocks))

    def test_forbidden_syntax_is_rejected_by_ast_whitelist(self):
        _, _, blocks = self._enriched()
        api = ReviewAPI(blocks)
        forbidden = {
            "import": "import os\n",
            "import_from": "from os import path\n",
            "function": "def f():\n    return 1\n",
            "class": "class X:\n    pass\n",
            "while": "while True:\n    pass\n",
            "lambda": "x = lambda y: y\n",
            "print": "print('x')\n",
            "open": "open('/tmp/x')\n",
            "getattr": "getattr(api, 'blocks')\n",
            "private_attr": "api._blocks\n",
            "dunder_attr": "api.__class__\n",
            "type_reflection": "type(api)\n",
            "pow": "x = 2 ** 100\n",
            "format_reflection": "x = '{0._blocks}'.format(api)\n",
            "try": "try:\n    pass\nexcept Exception:\n    pass\n",
        }
        for label, script in forbidden.items():
            with self.subTest(label=label):
                with self.assertRaises(DSLValidationError):
                    execute_dsl(script, api)

    def test_allowed_comprehension_and_emit_guard(self):
        _, task, blocks = self._enriched()
        api = ReviewAPI(blocks)
        script = """
pairs = [pair for pair in api.adjacent_body_pairs() if pair[0]["page"] == pair[1]["page"]]
for prev, curr in pairs:
    if prev["order"] == pairs[0][0]["order"]:
        api.emit_join(prev.id, curr.id, "first candidate only")
"""
        operations = execute_dsl(script, api)
        self.assertEqual(len(operations), 1)
        build_synthesis_review(task, operations)

        api2 = ReviewAPI(blocks)
        with self.assertRaises(ValueError):
            api2.emit_join("missing-id", "missing-id-2", "reason")
        with self.assertRaises(ValueError):
            api2.emit_join(blocks[1]["id"], blocks[2]["id"], "   ")

    def test_range_and_instruction_budgets_are_enforced(self):
        _, _, blocks = self._enriched()
        with self.assertRaises(RuntimeError):
            execute_dsl("for i in range(10001):\n    x = i\n", ReviewAPI(blocks))

        with self.assertRaises(TimeoutError):
            execute_dsl("x = 1\n", ReviewAPI(blocks), tick_limit=1)

    def test_conservation_rejects_non_join_and_unknown_blocks(self):
        _, task, blocks = self._enriched()
        pairs = ReviewAPI(blocks).adjacent_body_pairs()
        prev, curr = pairs[0]
        valid = [
            {
                "op": "join-blocks",
                "target_block_ids": [prev["id"], curr["id"]],
                "reason": "fixture join",
            }
        ]
        verify_join_conservation(task, valid)

        with self.assertRaises(ConservationError):
            verify_join_conservation(
                task,
                [{"op": "replace-markdown", "block_id": prev["id"]}],
            )
        with self.assertRaises(ConservationError):
            verify_join_conservation(
                task,
                [
                    {
                        "op": "join-blocks",
                        "target_block_ids": ["missing-a", "missing-b"],
                        "reason": "missing blocks",
                    }
                ],
            )

        forged = [dict(valid[0], extra_field=True)]
        with self.assertRaises(ContractValidationError):
            build_synthesis_review(task, forged)

    def test_enrich_task_blocks_pins_geometry_to_exact_article_model(self):
        model, task, blocks = self._enriched()
        body = task["blocks"][1]
        enriched = next(block for block in blocks if block["id"] == body["id"])
        self.assertEqual(
            enriched["bbox"],
            model["blocks"][1]["source_spans"][0]["bbox"],
        )
        self.assertNotIn("bbox", body)

        other_model = test_text_review.JoinBlocksContractTests()._model(
            joinable=False
        )
        with self.assertRaises(ContractValidationError):
            enrich_task_blocks(task, other_model)

    def test_synthesis_run_is_canonical_and_replayable(self):
        model, task, blocks = self._enriched()
        script = """
pairs = api.adjacent_body_pairs()
prev, curr = pairs[0]
if api.same_column(prev, curr):
    api.emit_join(prev.id, curr.id, "persisted same-column join")
"""
        operations = execute_dsl(script, ReviewAPI(blocks))
        review = build_synthesis_review(task, operations)
        run = build_synthesis_run(task, script, review)
        self.assertEqual(
            run["contract_version"], SYNTHESIS_RUN_CONTRACT_VERSION
        )
        self.assertEqual(run["source_sha256"], task["source_sha256"])
        self.assertEqual(
            run["article_model_sha256"], task["article_model"]["sha256"]
        )
        self.assertEqual(
            canonical_synthesis_run_json(run),
            canonical_synthesis_run_json(json.loads(canonical_synthesis_run_json(run))),
        )
        self.assertEqual(
            validate_synthesis_run(
                run, task=task, article_model=model, review=review
            ),
            operations,
        )

        no_emit = dict(run, script="x = len(api.blocks())\n")
        with self.assertRaisesRegex(SynthesisError, "重放"):
            validate_synthesis_run(
                no_emit, task=task, article_model=model, review=review
            )

        bad_hash = dict(run, review_sha256="0" * 64)
        with self.assertRaisesRegex(SynthesisError, "review hash"):
            validate_synthesis_run(
                bad_hash, task=task, article_model=model, review=review
            )

    def test_l1_tool_candidate_extraction_uses_shared_core(self):
        _, task = self._model_task()
        tool = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "tools" / "run_text_review.py")
        )
        tool_pairs = tool["extract_candidates"](task)
        self.assertEqual(
            [(previous["id"], current["id"]) for previous, current in tool_pairs],
            [
                (previous["id"], current["id"])
                for previous, current in join_candidates(task)
            ],
        )

    def test_word_bag_normalizes_hyphen_variants(self):
        api = ReviewAPI([])
        self.assertEqual(
            api.word_bag("multi\u2010 modal"),
            api.word_bag("multi- modal"),
        )
        self.assertEqual(api.word_bag("a  b\na"), {"a": 2, "b": 1})


if __name__ == "__main__":
    unittest.main()
