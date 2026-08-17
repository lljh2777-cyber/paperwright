from __future__ import annotations

from dataclasses import replace
import json
import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.layout_models import FinalLayout, LayoutCandidate
from paperwright.layout_review import (
    configure_layout_review_task,
    validate_layout_review,
)
from paperwright.visual_relations import (
    VISUAL_RELATION_PROMPT_VERSION,
    VISUAL_RELATION_REVIEW_VERSION,
    build_visual_relation_task,
    canonical_visual_relation_review_json,
    compile_visual_relation_review,
    normalize_visual_relation_review,
    validate_visual_relation_review,
)
from tests.test_layout_stage_a import _task


class VisualRelationContractTests(unittest.TestCase):
    def setUp(self):
        raw = _task()
        self.relation_task = build_visual_relation_task(raw)
        self.final_task = configure_layout_review_task(raw, "visual-direct")

    def _review(self, groups, discarded=()):
        return {
            "contract_version": VISUAL_RELATION_REVIEW_VERSION,
            "source_sha256": self.relation_task.source_sha256,
            "page": self.relation_task.page.to_dict(),
            "task_sha256": self.relation_task.deterministic_sha256(),
            "reviewer": "fixture-vision-model",
            "prompt_version": VISUAL_RELATION_PROMPT_VERSION,
            "groups": groups,
            "discarded_candidate_ids": list(discarded),
            "warnings": [],
        }

    @staticmethod
    def _group(
        group_id,
        candidate_ids,
        content_class,
        role,
        order,
        parent=None,
    ):
        return {
            "group_id": group_id,
            "candidate_ids": list(candidate_ids),
            "content_class": content_class,
            "role": role,
            "order": order,
            "parent_group_id": parent,
            "confidence": 0.9,
        }

    def test_groups_compile_to_deterministic_candidate_unions(self):
        review = self._review(
            [
                self._group("body", ("C01", "C02"), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
            ]
        )
        validate_visual_relation_review(review, self.relation_task)
        canonical = canonical_visual_relation_review_json(
            review,
            task=self.relation_task,
        )
        self.assertEqual(json.loads(canonical), review)

        layout_value = compile_visual_relation_review(
            review,
            relation_task=self.relation_task,
            final_task=self.final_task,
        )
        layout = FinalLayout.from_dict(layout_value)
        validate_layout_review(layout, self.final_task)
        body = next(item for item in layout.regions if item.role == "body")
        self.assertEqual(body.bbox.x, 0.05)
        self.assertAlmostEqual(body.bbox.right, 0.95)
        self.assertEqual(body.source_candidate_ids, ())

    def test_legacy_prompt_review_remains_readable(self):
        review = self._review(
            [
                self._group("body", ("C01", "C02"), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
            ]
        )
        review["prompt_version"] = "paperwright-visual-relations-prompt-v0.1"
        validate_visual_relation_review(review, self.relation_task)

    def test_issue_caption_bbox_becomes_read_only_anchor_candidate(self):
        raw = _task()
        issue = {
            "issue_id": "issue-p0002-001",
            "page_index": raw.page.page_index,
            "kind": "caption_visual_binding",
            "scope": {
                "type": "elements",
                "bbox": {
                    "x": 0.1,
                    "y": 0.81,
                    "width": 0.5,
                    "height": 0.03,
                },
                "candidate_ids": [],
                "element_ids": ["caption-element"],
                "related_page_indices": [],
            },
        }
        task = build_visual_relation_task(raw, issues=(issue,))
        anchors = [
            item
            for item in task.candidates
            if item.features.get("issue_anchor") is True
        ]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].source_element_ids, ("caption-element",))
        self.assertEqual(
            anchors[0].features["high_confidence_caption_kind"],
            "figure",
        )
        self.assertEqual(task.metadata["issue_anchor_ids"], ["issue-p0002-001"])

    def test_cross_page_caption_anchor_may_remain_parentless_on_one_page(self):
        raw = _task()
        issue = {
            "issue_id": "issue-cross-001",
            "page_index": raw.page.page_index,
            "kind": "cross_page_caption_visual_binding",
            "scope": {
                "type": "elements",
                "bbox": {
                    "x": 0.1,
                    "y": 0.05,
                    "width": 0.5,
                    "height": 0.03,
                },
                "candidate_ids": ["previous:C01"],
                "element_ids": ["caption-element"],
                "related_page_indices": [raw.page.page_index - 1],
            },
        }
        relation_task = build_visual_relation_task(raw, issues=(issue,))
        final_task = configure_layout_review_task(raw, "visual-direct")
        anchor = next(
            item
            for item in relation_task.candidates
            if item.features.get("cross_page_caption_anchor") is True
        )
        review = {
            **self._review([]),
            "task_sha256": relation_task.deterministic_sha256(),
            "groups": [
                self._group("body", ("C01", "C02"), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
                self._group(
                    "cross-caption",
                    (anchor.candidate_id,),
                    "text",
                    "caption",
                    3,
                    None,
                ),
            ],
        }
        validate_visual_relation_review(review, relation_task)
        layout = FinalLayout.from_dict(
            compile_visual_relation_review(
                review,
                relation_task=relation_task,
                final_task=final_task,
            )
        )
        caption = next(item for item in layout.regions if item.role == "caption")
        self.assertIsNone(caption.parent_region_id)

    def test_caption_of_relation_compiles_to_parent_and_action(self):
        candidates = list(self.relation_task.candidates)
        caption = candidates[1]
        candidates[1] = LayoutCandidate(
            candidate_id=caption.candidate_id,
            bbox=caption.bbox,
            source_element_ids=caption.source_element_ids,
            element_kinds=caption.element_kinds,
            features={
                **caption.features,
                "high_confidence_caption_kind": "figure",
            },
        )
        relation_task = replace(
            self.relation_task,
            candidates=tuple(candidates),
        )
        review = {
            **self._review([]),
            "task_sha256": relation_task.deterministic_sha256(),
            "groups": [
                self._group("body", ("C01",), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
                self._group(
                    "caption",
                    ("C02",),
                    "text",
                    "caption",
                    3,
                    "figure",
                ),
            ],
        }
        layout = compile_visual_relation_review(
            review,
            relation_task=relation_task,
            final_task=self.final_task,
        )
        caption_region = next(
            item for item in layout["regions"] if item["role"] == "caption"
        )
        self.assertEqual(caption_region["parent_region_id"], "r-figure")
        self.assertTrue(
            any(item["action"] == "attach-caption" for item in layout["actions"])
        )

    def test_rejects_missing_duplicate_and_discarded_caption_candidates(self):
        missing = self._review(
            [self._group("body", ("C01",), "text", "body", 1)],
            discarded=("C03",),
        )
        with self.assertRaisesRegex(ContractValidationError, "守恒"):
            validate_visual_relation_review(missing, self.relation_task)

        duplicate = self._review(
            [
                self._group("body1", ("C01", "C02"), "text", "body", 1),
                self._group("body2", ("C02",), "text", "body", 2),
            ],
            discarded=("C03",),
        )
        with self.assertRaisesRegex(ContractValidationError, "内容非法"):
            validate_visual_relation_review(duplicate, self.relation_task)

        caption = self.relation_task.candidates[1]
        caption_task = replace(
            self.relation_task,
            candidates=(
                self.relation_task.candidates[0],
                replace(
                    caption,
                    features={
                        **caption.features,
                        "high_confidence_caption_kind": "figure",
                    },
                ),
                self.relation_task.candidates[2],
            ),
        )
        discarded_caption = {
            **self._review([]),
            "task_sha256": caption_task.deterministic_sha256(),
            "groups": [
                self._group("body", ("C01",), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
            ],
            "discarded_candidate_ids": ["C02"],
        }
        with self.assertRaisesRegex(ContractValidationError, "caption"):
            validate_visual_relation_review(discarded_caption, caption_task)

    def test_normalizes_only_reading_order_structure(self):
        groups = [
            self._group("body", ("C01",), "text", "body", 7),
            self._group("margin", ("C02",), "exclude", "margin", 8),
            self._group("figure", ("C03",), "visual", "figure", 7),
        ]
        review = self._review(groups)
        normalized = normalize_visual_relation_review(
            review,
        )

        self.assertEqual(
            [item["order"] for item in normalized["groups"]],
            [1, None, 2],
        )
        self.assertEqual(
            [item["candidate_ids"] for item in normalized["groups"]],
            [["C01"], ["C02"], ["C03"]],
        )
        self.assertIn("normalized relation reading orders", normalized["warnings"][0])
        validate_visual_relation_review(normalized, self.relation_task)

    def test_compiler_clips_non_exclude_union_to_confirmed_roi(self):
        overflowing = replace(
            self.relation_task.candidates[2],
            bbox=replace(
                self.relation_task.candidates[2].bbox,
                height=0.50,
            ),
        )
        relation_task = replace(
            self.relation_task,
            candidates=(
                self.relation_task.candidates[0],
                self.relation_task.candidates[1],
                overflowing,
            ),
        )
        review = {
            **self._review([]),
            "task_sha256": relation_task.deterministic_sha256(),
            "groups": [
                self._group("body", ("C01", "C02"), "text", "body", 1),
                self._group("figure", ("C03",), "visual", "figure", 2),
            ],
        }
        layout = compile_visual_relation_review(
            review,
            relation_task=relation_task,
            final_task=self.final_task,
        )

        figure = next(item for item in layout["regions"] if item["role"] == "figure")
        self.assertAlmostEqual(figure["bbox"]["y"] + figure["bbox"]["height"], 0.82)
        self.assertTrue(any("clipped" in item for item in layout["warnings"]))
        add = next(
            item for item in layout["actions"] if item["result_region_ids"] == ["r-figure"]
        )
        self.assertIn("clipped", add["reason"])


if __name__ == "__main__":
    unittest.main()
