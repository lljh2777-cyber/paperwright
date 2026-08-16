from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.summarize_quality_baseline import build_baseline


class QualityBaselineToolTests(unittest.TestCase):
    def test_build_baseline_aggregates_routes_and_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_root = root / "batch"
            output = batch_root / "0001-paper"
            layout = root / "layout" / "paper"
            annotations = root / "annotations"
            output.mkdir(parents=True)
            layout.mkdir(parents=True)
            annotations.mkdir()

            (batch_root / "batch_summary.json").write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "input_name": "paper.pdf",
                                "input_sha256": "a" * 64,
                                "input_size_bytes": 123,
                                "output_dir": "0001-paper",
                                "status": "success",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "success_with_degradation",
                        "page_count": 2,
                        "warnings": [{"code": "warning-a"}],
                        "degraded": [{"code": "degraded-a"}],
                        "images": [{}, {}],
                        "figures": [{}],
                        "tables": [],
                        "equations": [{}],
                        "figure_rejections": [{}],
                    }
                ),
                encoding="utf-8",
            )
            (layout / "routing.json").write_text(
                json.dumps(
                    {
                        "source_sha256": "a" * 64,
                        "page_count": 2,
                        "summary": {"L0_RULE": 1, "L1_TEXT_MODEL": 1},
                    }
                ),
                encoding="utf-8",
            )
            (annotations / "a01.json").write_text(
                json.dumps(
                    {
                        "filename": "paper.pdf",
                        "sha256": "a" * 64,
                        "page_count": 2,
                        "native_text": {"available": True},
                        "layout_profile": "single-column",
                        "features": {},
                        "sampled_pages": [1, {"page": 2}],
                        "dimension_results": {
                            "text_integrity": "major",
                            "reading_order": {"status": "minor"},
                            "visual_completeness": {"result": "pass"},
                            "section_structure": "not_assessed",
                            "caption_binding": "not_assessed",
                            "furniture_exclusion": "not_assessed",
                            "provenance": "not_assessed",
                            "uncertainty_handling": "not_assessed",
                        },
                        "routing_observations": {},
                        "issues": [
                            {
                                "page": 1,
                                "category": "reading_order",
                                "severity": "major",
                                "source_evidence": "two columns",
                                "observed_output": "columns interleaved",
                                "likely_layer": "routing",
                                "recommended_action": "reorder",
                                "confidence": "high",
                            }
                        ],
                        "hallucination_count": 0,
                        "overall_status": "major",
                        "reviewer_model": "fixture",
                    }
                ),
                encoding="utf-8",
            )

            corpus, summary = build_baseline(
                batch_root, root / "layout", annotations
            )

            self.assertEqual(summary["documents_total"], 1)
            self.assertEqual(summary["pages_total"], 2)
            self.assertEqual(summary["annotated_documents"], 1)
            self.assertEqual(summary["sampled_page_count"], 2)
            self.assertEqual(summary["routing_counts"]["L0_RULE"], 1)
            self.assertEqual(summary["dimension_results"]["text_integrity"]["major"], 1)
            self.assertEqual(summary["dimension_results"]["visual_completeness"]["pass"], 1)
            self.assertEqual(summary["dimension_results"]["provenance"]["not_assessed"], 1)
            self.assertEqual(summary["issue_categories"]["reading_order"], 1)
            self.assertEqual(summary["issues_per_100_sampled_pages"], 50.0)
            self.assertEqual(summary["overall_statuses"]["major"], 1)
            self.assertEqual(corpus["documents"][0]["asset_counts"]["images"], 2)


if __name__ == "__main__":
    unittest.main()
