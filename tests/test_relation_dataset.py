from __future__ import annotations

from copy import deepcopy
import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.relation_dataset import (
    RELATION_DATASET_SCOPE,
    RELATION_DATASET_VERSION,
    relation_dataset_summary,
    validate_relation_dataset,
)


def _dataset():
    value = {
        "contract_version": RELATION_DATASET_VERSION,
        "dataset_id": "fixture-v0.1",
        "quality_tier": "silver",
        "scope": RELATION_DATASET_SCOPE,
        "documents": [
            {
                "document_id": "fixture-paper",
                "source_sha256": "a" * 64,
                "page_count": 2,
            }
        ],
        "examples": [
            {
                "example_id": "fixture-p1-p2",
                "document_id": "fixture-paper",
                "relation_scope": "cross_page_adjacent",
                "visual_page_index": 0,
                "caption_page_index": 1,
                "caption_kind": "figure",
                "label": "positive",
                "caption_evidence": {
                    "anchor_element_id": "caption",
                    "text_prefix": "Figure 1.",
                    "text_sha256": "b" * 64,
                    "normalized_y": 0.1,
                },
                "page_evidence": {
                    "visual_page_image_sha256": "c" * 64,
                    "caption_page_image_sha256": "d" * 64,
                    "signals": ["full_page_visual", "caption_on_next_page"],
                },
                "adjudication": {
                    "status": "seed_verified",
                    "reviewer": "fixture-reviewer",
                    "confidence": "high",
                    "rationale_code": "full_page_visual_then_caption",
                    "source_annotation": "fixture.json",
                },
            }
        ],
    }
    value["summary"] = relation_dataset_summary(value)
    return value


class RelationDatasetTests(unittest.TestCase):
    def test_accepts_hash_bound_silver_dataset(self):
        value = _dataset()
        validate_relation_dataset(value)
        self.assertEqual(value["summary"]["by_label"]["positive"], 1)

    def test_rejects_non_adjacent_cross_page_example(self):
        value = _dataset()
        value["documents"][0]["page_count"] = 3
        value["examples"][0]["caption_page_index"] = 2
        with self.assertRaises(ContractValidationError):
            validate_relation_dataset(value)

    def test_rejects_duplicate_example_id(self):
        value = _dataset()
        value["examples"].append(deepcopy(value["examples"][0]))
        value["summary"] = relation_dataset_summary(value)
        with self.assertRaises(ContractValidationError):
            validate_relation_dataset(value)

    def test_rejects_nonhuman_gold_example(self):
        value = _dataset()
        value["quality_tier"] = "gold"
        with self.assertRaises(ContractValidationError):
            validate_relation_dataset(value)

    def test_rejects_stale_summary(self):
        value = _dataset()
        value["summary"]["example_count"] = 99
        with self.assertRaises(ContractValidationError):
            validate_relation_dataset(value)


if __name__ == "__main__":
    unittest.main()
