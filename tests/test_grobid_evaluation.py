from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pypdfium2 as pdfium

from paperwright.exceptions import ContractValidationError
from paperwright.grobid_evaluation import (
    GROBID_AUDIT_TASK_VERSION,
    GROBID_EVAL_CORPUS_VERSION,
    build_grobid_audit_task,
    compare_grobid_review_summaries,
    summarize_grobid_review,
    validate_grobid_evaluation_corpus,
)
from pdf_fixture_factory import create_born_digital_fixture


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class GrobidEvaluationCorpusTests(unittest.TestCase):
    def test_validates_hash_page_and_native_text_bound_corpus(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "papers" / "fixture.pdf"
            source.parent.mkdir()
            create_born_digital_fixture(source)
            pdf = pdfium.PdfDocument(source)
            characters = 0
            for page in pdf:
                text_page = page.get_textpage()
                characters += len(text_page.get_text_range())
                text_page.close()
                page.close()
            page_count = len(pdf)
            pdf.close()
            corpus = {
                "contract_version": GROBID_EVAL_CORPUS_VERSION,
                "documents": [
                    {
                        "document_id": "fixture",
                        "candidate_position": 65,
                        "file": "papers/fixture.pdf",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "bytes": source.stat().st_size,
                        "page_count": page_count,
                        "native_text_chars": characters,
                    }
                ],
                "summary": {
                    "document_count": 1,
                    "page_count": page_count,
                    "native_text_chars": characters,
                    "total_bytes": source.stat().st_size,
                },
            }
            corpus_path = root / "CORPUS.json"
            _write_json(corpus_path, corpus)
            value, actual_root = validate_grobid_evaluation_corpus(corpus_path)
            self.assertEqual(value["documents"][0]["document_id"], "fixture")
            self.assertEqual(actual_root, root)

            source.write_bytes(source.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ContractValidationError, "hash"):
                validate_grobid_evaluation_corpus(corpus_path)


class GrobidEvaluationEvidenceTests(unittest.TestCase):
    def _review(self, root: Path) -> Path:
        review = root / "review"
        evidence = review / "source-evidence"
        provider = {
            "provider_id": "grobid-scholarly",
            "provider_version": "0.9.0",
            "status": "complete",
            "pages": [
                {
                    "page_index": 0,
                    "observations": [
                        {
                            "observation_id": "grobid-scholarly:n000000:s000",
                            "text": "Fixture Title",
                            "paperwright_bbox": {
                                "x": 10,
                                "y": 10,
                                "width": 100,
                                "height": 20,
                            },
                        },
                        {
                            "observation_id": "grobid-scholarly:n000001:s000",
                            "text": "Body text",
                            "paperwright_bbox": {
                                "x": 10,
                                "y": 50,
                                "width": 100,
                                "height": 20,
                            },
                        },
                    ],
                }
            ],
        }
        claims = {
            "claims": [
                {
                    "claim_id": "grobid-role-000000",
                    "provider_id": "grobid-scholarly",
                    "claim_type": "title",
                    "evidence_observation_ids": [
                        "grobid-scholarly:n000000:s000"
                    ],
                },
                {
                    "claim_id": "grobid-role-000001",
                    "provider_id": "grobid-scholarly",
                    "claim_type": "paragraph",
                    "evidence_observation_ids": [
                        "grobid-scholarly:n000001:s000"
                    ],
                },
            ]
        }
        alignments = {
            "alignments": [
                {
                    "provider_id": "grobid-scholarly",
                    "observation_id": "grobid-scholarly:n000000:s000",
                    "physical_element_id": "p0000-text-00000",
                    "text_score": 1.0,
                    "geometry_score": 0.9,
                }
            ]
        }
        conflicts = {
            "conflicts": [
                {
                    "conflict_id": "conflict-1",
                    "claim_ids": ["grobid-role-000001"],
                    "observation_ids": [],
                }
            ]
        }
        requests = {
            "requests": [{"conflict_id": "conflict-1", "request_id": "request-1"}]
        }
        recipe = {
            "actions": [
                {
                    "action_id": "action-1",
                    "role": "title",
                    "evidence_refs": ["grobid-role-000000"],
                },
                {
                    "action_id": "action-2",
                    "role": "body",
                    "evidence_refs": ["pdfium-native:p0000:text:00000"],
                },
            ]
        }
        _write_json(evidence / "providers" / "grobid-scholarly.json", provider)
        _write_json(evidence / "claims.json", claims)
        _write_json(evidence / "alignments.json", alignments)
        _write_json(evidence / "conflicts.json", conflicts)
        _write_json(evidence / "specialist-requests.json", requests)
        _write_json(review / "paper-recipe.json", recipe)
        page_image = review / "page-0001" / "page.png"
        page_image.parent.mkdir()
        page_image.write_bytes(b"fixture-page-image")
        return review

    def test_summarizes_alignment_and_downstream_use_by_claim_type(self):
        with tempfile.TemporaryDirectory() as temp:
            review = self._review(Path(temp))
            summary = summarize_grobid_review(review)
            self.assertEqual(summary["claim_count"], 2)
            self.assertEqual(
                summary["by_claim_type"]["title"]["native_alignment_support"],
                1.0,
            )
            self.assertEqual(
                summary["by_claim_type"]["paragraph"]["native_alignment_support"],
                0.0,
            )
            self.assertEqual(
                summary["by_claim_type"]["title"][
                    "alignment_weighted_text_coverage"
                ],
                1.0,
            )
            self.assertEqual(summary["grobid_related_conflict_count"], 1)
            self.assertEqual(summary["grobid_related_specialist_request_count"], 1)
            self.assertEqual(summary["grobid_referenced_recipe_action_count"], 1)

    def test_audit_task_hides_downstream_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            review = self._review(Path(temp))
            task = build_grobid_audit_task(
                review,
                document_id="fixture",
                source_sha256="a" * 64,
            )
            self.assertEqual(task["contract_version"], GROBID_AUDIT_TASK_VERSION)
            self.assertFalse(task["downstream_adoption_disclosed"])
            self.assertEqual(task["claims"][0]["segments"][0]["page_index"], 0)
            self.assertEqual(task["page_images"][0]["page_index"], 0)
            self.assertEqual(task["page_images"][0]["path"], "page-0001/page.png")
            self.assertNotIn("recipe", json.dumps(task))

    def test_comparison_reports_deltas_without_claiming_quality(self):
        native = {
            "all_conflict_count": 2,
            "all_specialist_request_count": 1,
            "all_recipe_action_count": 5,
            "grobid_referenced_recipe_action_count": 0,
        }
        grobid = {
            "all_conflict_count": 4,
            "all_specialist_request_count": 3,
            "all_recipe_action_count": 9,
            "grobid_referenced_recipe_action_count": 4,
        }
        result = compare_grobid_review_summaries(native, grobid)
        self.assertEqual(
            result["count_deltas_grobid_minus_native"]["all_conflict_count"],
            2,
        )
        self.assertFalse(result["quality_improvement_inferred"])


if __name__ == "__main__":
    unittest.main()
