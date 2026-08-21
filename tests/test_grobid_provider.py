import tempfile
import unittest
from pathlib import Path

from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.grobid_provider import (
    build_grobid_evidence_from_tei,
    unavailable_grobid_snapshot,
)

from pdf_fixture_factory import create_born_digital_fixture


class GrobidProviderTests(unittest.TestCase):
    def test_unconfigured_provider_is_explicitly_unavailable(self):
        snapshot = unavailable_grobid_snapshot("a" * 64)

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["observation_count"], 0)
        self.assertIn("scholarly_semantic_roles", snapshot["missing_capabilities"])

    def test_coordinate_tei_becomes_proposed_claims_aligned_to_native_text(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fixture.pdf"
            create_born_digital_fixture(source)
            document = PDFiumBackend().extract_inventory(
                source,
                PaperWrightConfig(),
            ).document
            title = next(
                item
                for item in document.pages[0].elements
                if item.kind == "text" and "Fixture Title" in (item.text or "")
            )
            body = next(
                item
                for item in document.pages[0].elements
                if item.kind == "text" and "born-digital" in (item.text or "")
            )

            def coords(item):
                box = item.bbox
                return f"1,{box.x},{box.y},{box.width},{box.height}"

            tei = f"""<TEI xmlns="http://www.tei-c.org/ns/1.0">
              <teiHeader><fileDesc><titleStmt><title coords="{coords(title)}">PaperWright Fixture Title</title></titleStmt></fileDesc>
              <profileDesc><abstract><p coords="{coords(body)}">A born-digital paragraph with Café.</p></abstract></profileDesc></teiHeader>
              <text><body><div><head coords="{coords(title)}">Methods</head><p coords="{coords(body)}">A born-digital paragraph with Café.</p></div></body></text>
            </TEI>"""

            snapshot, alignments, claims = build_grobid_evidence_from_tei(
                tei,
                document,
                provider_version="fixture",
            )

            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(snapshot["observation_count"], 4)
            self.assertEqual(len(alignments), 4)
            self.assertEqual(
                {claim["claim_type"] for claim in claims},
                {"title", "abstract", "section_heading", "paragraph"},
            )
            self.assertTrue(
                all(
                    claim["status"] == "proposed"
                    and claim["payload"]["direct_text_authority"] is False
                    for claim in claims
                )
            )


if __name__ == "__main__":
    unittest.main()
