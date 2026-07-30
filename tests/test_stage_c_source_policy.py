import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fetch_stage_c_oa import validate_download_url  # noqa: E402


class StageCSourcePolicyTests(unittest.TestCase):
    def test_all_frozen_pdf_urls_use_approved_https_hosts(self):
        records = json.loads(
            (ROOT / "realworld" / "oa_sources.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(records["papers"]), 8)
        self.assertEqual(records["totals"]["page_count"], 138)
        self.assertEqual(records["totals"]["unique_pdf_sha256"], 8)
        for record in records["papers"]:
            validate_download_url(record["pdf_url"])
            self.assertEqual(record["license"], "CC-BY-4.0")
            self.assertFalse(record["pdf_redistributed"])
            self.assertEqual(len(record["sha256"]), 64)

    def test_unapproved_or_credentialed_urls_are_rejected(self):
        for value in (
            "http://journals.plos.org/example.pdf",
            "https://example.invalid/paper.pdf",
            "https://user:secret@europepmc.org/paper.pdf",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_download_url(value)


if __name__ == "__main__":
    unittest.main()
