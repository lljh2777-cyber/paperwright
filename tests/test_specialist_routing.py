import tempfile
import unittest
from pathlib import Path

from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.source_evidence import build_pdfium_source_evidence
from paperwright.specialist_routing import derive_specialist_requests

from pdf_fixture_factory import create_born_digital_fixture


class SpecialistRoutingTests(unittest.TestCase):
    def _document(self, root: Path):
        source = root / "fixture.pdf"
        create_born_digital_fixture(source)
        return PDFiumBackend().extract_inventory(
            source,
            PaperWrightConfig(),
        ).document

    def test_uncorroborated_table_proposal_routes_only_its_roi(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))
            index, artifacts = build_pdfium_source_evidence(document)
            del index
            pdfium = artifacts["providers/pdfium-native.json"]
            evidence_id = pdfium["pages"][0]["observations"][0]["observation_id"]
            table_claim = {
                "claim_id": "table-one",
                "provider_id": "pdfplumber-geometry",
                "capability": "table_proposal",
                "claim_type": "table_region",
                "evidence_observation_ids": [evidence_id],
                "payload": {
                    "page_index": 0,
                    "paperwright_bbox": {
                        "x": 20.0,
                        "y": 30.0,
                        "width": 100.0,
                        "height": 80.0,
                    },
                },
                "status": "proposed",
            }

            conflicts, requests = derive_specialist_requests(
                document,
                {"pdfium-native": pdfium},
                [table_claim],
            )

            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["kind"], "table_boundary_or_structure")
            self.assertEqual(requests[0]["scope"]["page_indices"], [0])
            self.assertEqual(
                requests[0]["scope"]["paperwright_bbox"],
                table_claim["payload"]["paperwright_bbox"],
            )
            self.assertNotIn(1, requests[0]["scope"]["page_indices"])

    def test_grobid_table_overlap_corroborates_pdfplumber_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))
            _, artifacts = build_pdfium_source_evidence(document)
            pdfium = artifacts["providers/pdfium-native.json"]
            observation = pdfium["pages"][0]["observations"][0]
            evidence_id = observation["observation_id"]
            bbox = observation["paperwright_bbox"]
            claims = [
                {
                    "claim_id": "table-one",
                    "provider_id": "pdfplumber-geometry",
                    "claim_type": "table_region",
                    "evidence_observation_ids": [evidence_id],
                    "payload": {"page_index": 0, "paperwright_bbox": bbox},
                },
                {
                    "claim_id": "grobid-table-one",
                    "provider_id": "grobid-scholarly",
                    "claim_type": "table",
                    "evidence_observation_ids": [evidence_id],
                    "payload": {"page_indices": [0]},
                },
            ]

            conflicts, requests = derive_specialist_requests(
                document,
                {"pdfium-native": pdfium},
                claims,
            )

            self.assertEqual(conflicts, [])
            self.assertEqual(requests, [])

    def test_severe_raster_without_native_visuals_routes_full_page(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))
            _, artifacts = build_pdfium_source_evidence(document)
            pdfium = artifacts["providers/pdfium-native.json"]
            pdfium["pages"][1]["observations"] = [
                item
                for item in pdfium["pages"][1]["observations"]
                if item["kind"] == "text"
            ]

            conflicts, requests = derive_specialist_requests(
                document,
                {"pdfium-native": pdfium},
                [],
                raster_analyses={1: {"coverage": {"residual": 0.12}}},
            )

            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["kind"], "native_object_raster_mismatch")
            self.assertEqual(requests[0]["scope"]["page_indices"], [1])
            self.assertEqual(
                requests[0]["scope"]["paperwright_bbox"],
                {
                    "x": 0.0,
                    "y": 0.0,
                    "width": document.pages[1].width,
                    "height": document.pages[1].height,
                },
            )

    def test_large_grobid_order_inversion_routes_reading_order_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))
            _, artifacts = build_pdfium_source_evidence(document)
            pdfium = artifacts["providers/pdfium-native.json"]
            native = [
                item
                for item in pdfium["pages"][0]["observations"]
                if item["kind"] == "text"
            ][:4]
            grobid_observations = []
            for sequence, item in enumerate(reversed(native)):
                grobid_observations.append(
                    {
                        "observation_id": f"grobid-scholarly:test:{sequence}",
                        "physical_element_id": item["physical_element_id"],
                        "paperwright_bbox": item["paperwright_bbox"],
                    }
                )
            grobid = {
                "status": "complete",
                "pages": [
                    {
                        "page_index": 0,
                        "observations": grobid_observations,
                    }
                ],
            }

            conflicts, requests = derive_specialist_requests(
                document,
                {
                    "pdfium-native": pdfium,
                    "grobid-scholarly": grobid,
                },
                [],
            )

            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["kind"], "multi_provider_reading_order")
            self.assertEqual(conflicts[0]["metrics"]["inversion_count"], 6)
            self.assertEqual(requests[0]["scope"]["page_indices"], [0])


if __name__ == "__main__":
    unittest.main()
