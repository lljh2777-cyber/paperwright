import unittest

from paper2md.evidence import (
    build_validation_report,
    build_warning_summary,
    validation_report_markdown,
)


class EvidenceWarningSummaryTests(unittest.TestCase):
    def test_warning_summary_groups_and_locates_quality_findings(self):
        summary = build_warning_summary(
            warnings=[
                {"code": "quality_markdown_text_suspicions"},
                {
                    "code": "runtime_warning",
                    "page": 3,
                    "region_id": "R3",
                    "reason": "fixture",
                },
            ],
            quality_checks={
                "markdown_text": {
                    "status": "warning",
                    "findings": [
                        {
                            "code": "short_body_fragment",
                            "page": 3,
                            "region_id": "R3",
                            "paragraph_index": 2,
                            "snippet": "small fragment",
                        }
                    ],
                },
                "image_links": {"status": "pass"},
            },
        )
        self.assertEqual(summary["issue_count"], 2)
        self.assertEqual(summary["affected_pages"], [3])
        self.assertEqual(summary["by_severity"], {"warning": 2})
        self.assertEqual(
            summary["by_warning_code"],
            {
                "quality_markdown_text_suspicions": 1,
                "runtime_warning": 1,
            },
        )
        self.assertEqual(
            summary["actionable_findings"][0]["paragraph_index"], 2
        )

    def test_failed_check_without_findings_remains_actionable(self):
        summary = build_warning_summary(
            warnings=[],
            quality_checks={"image_links": {"status": "fail"}},
        )
        self.assertEqual(summary["by_severity"], {"error": 1})
        self.assertEqual(
            summary["actionable_findings"][0]["code"],
            "image_links_fail",
        )

    def test_report_and_markdown_expose_compact_summary(self):
        report = build_validation_report(
            status="success_with_degradation",
            evidence_level="standard",
            page_count=4,
            image_count=1,
            warnings=[{"code": "fixture_warning"}],
            references={"mode": "omit"},
            reviewers=["fixture"],
            quality_checks={
                "markdown_text": {
                    "status": "warning",
                    "findings": [
                        {
                            "code": "short_body_fragment",
                            "page": 2,
                            "region_id": "R2",
                            "paragraph_index": 0,
                            "snippet": "A fragment",
                        }
                    ],
                }
            },
        )
        self.assertEqual(
            report["contract_version"],
            "paper2md-validation-report-v0.2",
        )
        self.assertEqual(report["warning_summary"]["affected_pages"], [2])
        markdown = validation_report_markdown(report)
        self.assertIn("## 可操作问题", markdown)
        self.assertIn("short_body_fragment", markdown)
        self.assertIn("page=2", markdown)
        self.assertIn("warning=1", markdown)


if __name__ == "__main__":
    unittest.main()
