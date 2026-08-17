from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

from paperwright.layout_review import configure_layout_review_task
from paperwright.llm_cost import CostReport
from paperwright.synthesize import canonical_synthesis_run_json
from paperwright.issue_routing import IssueRoutingPlan, RoutedIssue
from paperwright.text_review import (
    build_text_task,
    canonical_text_review_json,
    validate_text_review,
)
from tests import test_routing, test_text_review
from tests.test_layout_stage_a import _task as _layout_task
from paperwright.visual_relations import build_visual_relation_task


ROOT = Path(__file__).resolve().parents[1]
L1_TOOL = runpy.run_path(str(ROOT / "tools" / "run_text_review.py"))
L3_TOOL = runpy.run_path(str(ROOT / "tools" / "run_text_synthesize.py"))
VISUAL_TOOL = runpy.run_path(str(ROOT / "tools" / "run_visual_review.py"))
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

    def test_issue_routing_filters_join_candidates_by_source_bbox(self):
        model, task = self._task()
        issue = RoutedIssue(
            issue_id="issue-p0001-001",
            page_index=0,
            kind="paragraph_continuation",
            stage="text",
            route="L1_TEXT_MODEL",
            fallback_route="L3_PROGRAM_SYNTHESIS",
            severity="suspicious",
            reason="localized fixture pair",
            signals=("pair_index:1",),
            scope={
                "type": "elements",
                "bbox": {"x": 0.1, "y": 0.35, "width": 0.4, "height": 0.02},
                "candidate_ids": [],
                "element_ids": ["fixture-current"],
                "related_page_indices": [],
            },
        )
        plan = IssueRoutingPlan(
            source_sha256=task["source_sha256"],
            page_count=1,
            issues=(issue,),
        ).to_dict()
        candidates = L1_TOOL["extract_candidates"](
            task,
            issue_routing=plan,
            article_model=model,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1]["id"], "blk_76c2f57797f095503423692c")

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


class VisualBridgeIssueContextTests(unittest.TestCase):
    def test_prompt_expands_only_local_issue_evidence(self):
        task = test_routing._task(
            0,
            "a" * 64,
            metadata={
                "analysis_roi": {
                    "bbox": {
                        "x": 0.05,
                        "y": 0.05,
                        "width": 0.9,
                        "height": 0.9,
                    }
                }
            },
        )
        issue = {
            "issue_id": "issue-p0001-001",
            "kind": "caption_visual_binding",
            "reason": "caption lacks a visual candidate",
            "signals": ["vector_count:42"],
            "scope": {
                "type": "candidates",
                "bbox": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.1},
                "candidate_ids": ["C001"],
                "element_ids": ["e-caption"],
                "related_page_indices": [],
            },
        }
        prompt = VISUAL_TOOL["_prompt"](task, [issue])
        self.assertIn("issue-p0001-001", prompt)
        self.assertIn("caption_visual_binding", prompt)
        self.assertIn("vector_count:42", prompt)
        self.assertIn("still returning a complete valid page layout", prompt)

    def test_relation_protocol_compiles_groups_instead_of_model_boxes(self):
        raw_task = _layout_task()
        relation_task = build_visual_relation_task(raw_task)
        final_task = configure_layout_review_task(raw_task, "visual-direct")
        response_value = {
            "groups": [
                {
                    "group_id": "body",
                    "candidate_ids": ["C01"],
                    "content_class": "text",
                    "role": "body",
                    "order": 1,
                    "parent_group_id": None,
                    "confidence": 0.9,
                },
                {
                    "group_id": "figure",
                    "candidate_ids": ["C03"],
                    "content_class": "visual",
                    "role": "figure",
                    "order": 2,
                    "parent_group_id": None,
                    "confidence": 0.9,
                },
                {
                    "group_id": "caption",
                    "candidate_ids": ["C02"],
                    "content_class": "text",
                    "role": "caption",
                    "order": 3,
                    "parent_group_id": "figure",
                    "confidence": 0.8,
                },
            ],
            "discarded_candidate_ids": [],
            "warnings": [],
        }
        completion = mock.Mock()
        completion.choices = [
            mock.Mock(
                message=mock.Mock(
                    content=json.dumps(response_value, ensure_ascii=False)
                )
            )
        ]
        completion.usage = None
        client = mock.Mock()
        client.chat.completions.create.return_value = completion
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "overlay.png"
            Image.new("RGB", (40, 40), "white").save(image_path)
            layout, review = VISUAL_TOOL["_generate_relation_layout"](
                client,
                relation_task,
                final_task,
                image_path,
                "fixture-model",
                CostReport(),
            )
        prompt = client.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ][0]["text"]
        self.assertIn("Do NOT draw or modify any bbox", prompt)
        self.assertNotIn('"bbox"', json.dumps(response_value))
        self.assertEqual(review["groups"][1]["candidate_ids"], ["C03"])
        caption = next(
            item for item in layout["regions"] if item["role"] == "caption"
        )
        self.assertEqual(caption["parent_region_id"], "r-figure")

    def test_relation_retry_includes_exact_validation_failure(self):
        raw_task = _layout_task()
        relation_task = build_visual_relation_task(raw_task)
        final_task = configure_layout_review_task(raw_task, "visual-direct")

        def response(value):
            completion = mock.Mock()
            completion.choices = [
                mock.Mock(message=mock.Mock(content=json.dumps(value)))
            ]
            completion.usage = None
            return completion

        missing = {
            "groups": [
                {
                    "group_id": "body",
                    "candidate_ids": ["C01", "C02"],
                    "content_class": "text",
                    "role": "body",
                    "order": 1,
                    "parent_group_id": None,
                    "confidence": 0.9,
                }
            ],
            "discarded_candidate_ids": [],
            "warnings": [],
        }
        valid = {
            **missing,
            "groups": [
                missing["groups"][0],
                {
                    "group_id": "figure",
                    "candidate_ids": ["C03"],
                    "content_class": "visual",
                    "role": "figure",
                    "order": 2,
                    "parent_group_id": None,
                    "confidence": 0.9,
                },
            ],
        }
        client = mock.Mock()
        client.chat.completions.create.side_effect = [
            response(missing),
            response(valid),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            page_dir = Path(temp_dir) / "page-0002"
            page_dir.mkdir()
            (page_dir / "layout-task.json").write_text(
                final_task.canonical_json(),
                encoding="utf-8",
            )
            (page_dir / "visual-relation-task.json").write_text(
                relation_task.canonical_json(),
                encoding="utf-8",
            )
            Image.new("RGB", (40, 40), "white").save(
                page_dir / "candidate-overlay.png"
            )
            layout, review = VISUAL_TOOL["_review_page"](
                client,
                page_dir,
                "fixture-model",
                {"pages": []},
                CostReport(),
                attempts=2,
            )

        self.assertEqual(review["groups"][1]["candidate_ids"], ["C03"])
        self.assertEqual(len(layout["regions"]), 2)
        second_prompt = client.chat.completions.create.call_args_list[1].kwargs[
            "messages"
        ][0]["content"][0]["text"]
        self.assertIn("candidate accounting", second_prompt)
        self.assertIn("missing=C03", second_prompt)
        self.assertIn("corrected COMPLETE response", second_prompt)


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
