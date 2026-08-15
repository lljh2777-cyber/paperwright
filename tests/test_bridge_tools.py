from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

from paperwright.synthesize import canonical_synthesis_run_json
from paperwright.text_review import (
    build_text_task,
    canonical_text_review_json,
    validate_text_review,
)
from tests import test_text_review


ROOT = Path(__file__).resolve().parents[1]
L1_TOOL = runpy.run_path(str(ROOT / "tools" / "run_text_review.py"))
L3_TOOL = runpy.run_path(str(ROOT / "tools" / "run_text_synthesize.py"))
# runpy returns a shallow copy of the module namespace; patch through the
# function's live globals so main() sees the replacement.
L1_GLOBALS = L1_TOOL["main"].__globals__


class _FakeCompletions:
    def __init__(self, decisions, seen_models=None):
        self.decisions = decisions
        self.seen_models = seen_models if seen_models is not None else []

    def create(self, **kwargs):
        self.seen_models.append(kwargs.get("model"))
        content = json.dumps({"decisions": self.decisions})
        message = mock.Mock()
        message.content = content
        choice = mock.Mock()
        choice.message = message
        response = mock.Mock()
        response.choices = [choice]
        return response


class _FakeClient:
    def __init__(self, decisions, seen_models=None):
        self.chat = mock.Mock()
        self.chat.completions = _FakeCompletions(decisions, seen_models)


class L1BridgeToolTests(unittest.TestCase):
    def _task(self):
        model = test_text_review.JoinBlocksContractTests()._model()
        return model, build_text_task(model)

    def test_judge_uses_requested_model(self):
        _, task = self._task()
        candidates = L1_TOOL["extract_candidates"](task)
        seen = []
        client = _FakeClient(
            [
                {"index": 0, "verdict": "DIFFERENT_PARAGRAPHS", "reason": "separate"},
                {"index": 1, "verdict": "DIFFERENT_PARAGRAPHS", "reason": "separate"},
            ],
            seen,
        )
        decisions, _cost = L1_TOOL["judge"](
            client, candidates, model="custom-model"
        )
        self.assertEqual(len(decisions), 2)
        self.assertEqual(seen, ["custom-model"])

    def test_build_review_validates_and_matches_canonical_contract(self):
        _, task = self._task()
        candidates = L1_TOOL["extract_candidates"](task)
        decisions = [
            {
                "_batch_offset": 0,
                "index": 0,
                "verdict": "SAME_PARAGRAPH",
                "reason": "Same paragraph split at a column boundary.",
            },
            {
                "_batch_offset": 0,
                "index": 1,
                "verdict": "DIFFERENT_PARAGRAPHS",
                "reason": "separate paragraph",
            },
        ]
        review = L1_TOOL["build_review"](task, candidates, decisions)
        self.assertEqual(len(review["operations"]), 1)
        validate_text_review(review, task=task)
        canonical = canonical_text_review_json(review, task=task)
        self.assertEqual(
            canonical,
            canonical_text_review_json(
                json.loads(canonical), task=task
            ),
        )

    def test_build_review_rejects_invalid_model_decisions(self):
        _, task = self._task()
        candidates = L1_TOOL["extract_candidates"](task)
        invalid = [
            [{"verdict": "SAME_PARAGRAPH", "reason": "r"}],  # missing index
            [{"_batch_offset": 0, "index": 0, "verdict": "MAYBE", "reason": "r"}],
            [
                {"_batch_offset": 0, "index": 0, "verdict": "SAME_PARAGRAPH", "reason": "a"},
                {"_batch_offset": 0, "index": 0, "verdict": "DIFFERENT_PARAGRAPHS", "reason": "b"},
            ],
            [{"_batch_offset": 0, "index": 99, "verdict": "SAME_PARAGRAPH", "reason": "r"}],
        ]
        for decisions in invalid:
            with self.subTest(decisions=decisions):
                with self.assertRaises(ValueError):
                    L1_TOOL["build_review"](task, candidates, decisions)

    def test_main_uses_model_flag_writes_canonical_and_refuses_overwrite(self):
        model, task = self._task()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "text-task.json"
            review_path = root / "text-review.json"
            task_path.write_text(
                json.dumps(task, ensure_ascii=False, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            seen = []
            client = _FakeClient(
                [
                    {"index": 0, "verdict": "SAME_PARAGRAPH", "reason": "column split"},
                    {"index": 1, "verdict": "DIFFERENT_PARAGRAPHS", "reason": "separate"},
                ],
                seen,
            )
            original_load_client = L1_GLOBALS["_load_client"]
            L1_GLOBALS["_load_client"] = lambda: client
            old_argv = sys.argv
            sys.argv = [
                "run_text_review.py",
                str(task_path),
                str(review_path),
                "--model",
                "override-model",
            ]
            try:
                self.assertEqual(L1_TOOL["main"](), 0)
            finally:
                sys.argv = old_argv
                L1_GLOBALS["_load_client"] = original_load_client
            self.assertEqual(seen, ["override-model"])
            review = json.loads(review_path.read_text(encoding="utf-8"))
            validate_text_review(review, task=task)
            self.assertEqual(
                review_path.read_text(encoding="utf-8"),
                canonical_text_review_json(review, task=task),
            )
            self.assertTrue(
                (root / "text-review.usage.json").is_file(),
                "L1 bridge should persist a usage report",
            )

            # Refusal happens before the model client is created.
            called = []
            original_load_client = L1_GLOBALS["_load_client"]
            L1_GLOBALS["_load_client"] = lambda: called.append(True)
            sys.argv = [
                "run_text_review.py",
                str(task_path),
                str(review_path),
            ]
            try:
                with self.assertRaises(SystemExit):
                    L1_TOOL["main"]()
            finally:
                sys.argv = old_argv
                L1_GLOBALS["_load_client"] = original_load_client
            self.assertEqual(called, [])


class L3BridgeToolTests(unittest.TestCase):
    def test_output_pair_is_atomic_and_rolls_back_on_second_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_path = root / "text-review.json"
            run_path = root / "synthesize-run.json"
            L3_TOOL["_write_outputs_atomic"](
                review_path, "review-text", run_path, "run-text"
            )
            self.assertEqual(review_path.read_text(encoding="utf-8"), "review-text")
            self.assertEqual(run_path.read_text(encoding="utf-8"), "run-text")

            real_replace = os.replace
            second_review = root / "second-review.json"
            second_run = root / "second-run.json"
            with mock.patch(
                "os.replace",
                side_effect=[real_replace, OSError("simulated second replace failure")],
            ):
                with self.assertRaises(OSError):
                    L3_TOOL["_write_outputs_atomic"](
                        second_review, "second-review", second_run, "second-run"
                    )
            self.assertFalse(second_review.exists())
            self.assertFalse(second_run.exists())
            self.assertEqual(review_path.read_text(encoding="utf-8"), "review-text")
            self.assertEqual(run_path.read_text(encoding="utf-8"), "run-text")

    def test_script_mode_writes_valid_review_and_run_pair(self):
        model, task = self._task_and_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "article-model.json"
            task_path = root / "text-task.json"
            review_path = root / "text-review.json"
            run_path = root / "synthesize-run.json"
            script_path = root / "join.dsl"
            model_path.write_text(
                json.dumps(model, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            task_path.write_text(
                json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            script_path.write_text(self._script, encoding="utf-8")

            old_argv = sys.argv
            sys.argv = [
                "run_text_synthesize.py",
                str(model_path),
                str(task_path),
                str(review_path),
                "--script",
                str(script_path),
                "--synthesis-run",
                str(run_path),
            ]
            try:
                self.assertEqual(L3_TOOL["main"](), 0)
            finally:
                sys.argv = old_argv

            review = json.loads(review_path.read_text(encoding="utf-8"))
            validate_text_review(review, task=task)
            run = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(
                run_path.read_text(encoding="utf-8"),
                canonical_synthesis_run_json(run),
            )
            self.assertEqual(run["operation_count"], 1)

    @staticmethod
    def _task_and_script():
        model = test_text_review.JoinBlocksContractTests()._model()
        task = build_text_task(model)
        return model, task

    _script = """
pairs = api.adjacent_body_pairs()
prev, curr = pairs[0]
if api.same_column(prev, curr):
    api.emit_join(prev.id, curr.id, "same column continuation")
"""


if __name__ == "__main__":
    unittest.main()
