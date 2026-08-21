import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.docling_provider import (
    build_docling_evidence_from_documents,
    unavailable_docling_snapshot,
)
from paperwright.source_evidence import (
    validate_source_evidence_bundle,
    write_pdfium_source_evidence,
)

from pdf_fixture_factory import create_born_digital_fixture


class DoclingProviderTests(unittest.TestCase):
    def _document(self, root: Path):
        source = root / "fixture.pdf"
        create_born_digital_fixture(source)
        return PDFiumBackend().extract_inventory(
            source,
            PaperWrightConfig(),
        ).document

    def test_not_requested_is_distinct_from_no_layout_objects(self):
        snapshot = unavailable_docling_snapshot(
            "a" * 64,
            reason="docling_not_requested_no_conflicts",
            requests=[],
        )

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["diagnostics"][0]["request_count"], 0)
        self.assertEqual(
            snapshot["diagnostics"][0]["code"],
            "docling_not_requested_no_conflicts",
        )

    def test_exported_document_is_filtered_to_requested_roi_and_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))
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
            table_box = {"l": 60.0, "t": 162.0, "r": 300.0, "b": 217.0}
            exported = {
                "body": {
                    "children": [
                        {"$ref": "#/texts/0"},
                        {"$ref": "#/texts/1"},
                        {"$ref": "#/tables/0"},
                    ]
                },
                "texts": [
                    {
                        "self_ref": "#/texts/0",
                        "label": "title",
                        "text": title.text,
                        "prov": [
                            {
                                "page_no": 1,
                                "bbox": {
                                    "l": title.bbox.x,
                                    "t": title.bbox.y,
                                    "r": title.bbox.right,
                                    "b": title.bbox.bottom,
                                    "coord_origin": "TOPLEFT",
                                },
                                "charspan": [0, len(title.text or "")],
                            }
                        ],
                    },
                    {
                        "self_ref": "#/texts/1",
                        "label": "paragraph",
                        "text": body.text,
                        "prov": [
                            {
                                "page_no": 1,
                                "bbox": {
                                    "l": body.bbox.x,
                                    "t": body.bbox.y,
                                    "r": body.bbox.right,
                                    "b": body.bbox.bottom,
                                    "coord_origin": "TOPLEFT",
                                },
                            }
                        ],
                    },
                ],
                "tables": [
                    {
                        "self_ref": "#/tables/0",
                        "label": "table",
                        "prov": [
                            {
                                "page_no": 1,
                                "bbox": {**table_box, "coord_origin": "TOPLEFT"},
                            }
                        ],
                        "data": {"num_rows": 2, "num_cols": 2, "table_cells": []},
                    }
                ],
            }
            request = {
                "request_id": "specialist-one",
                "provider_id": "docling-local",
                "conflict_id": "conflict-one",
                "scope": {
                    "page_indices": [0],
                    "paperwright_bbox": {
                        "x": 50.0,
                        "y": 150.0,
                        "width": 270.0,
                        "height": 85.0,
                    },
                },
                "requested_capabilities": ["table_structure"],
                "status": "requested",
            }

            snapshot, alignments, claims, statuses = (
                build_docling_evidence_from_documents(
                    [exported],
                    document,
                    [request],
                )
            )

            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(snapshot["observation_count"], 1)
            self.assertEqual(alignments, [])
            self.assertEqual(statuses, {"specialist-one": "completed"})
            self.assertEqual(
                {claim["claim_type"] for claim in claims},
                {"table_region"},
            )
            subset = snapshot["docling_document_subset"]
            self.assertTrue(subset["selected_only"])
            self.assertEqual(len(subset["items"]), 1)
            self.assertEqual(subset["items"][0]["table_data"]["num_rows"], 2)
            self.assertIn("provenance", subset["items"][0])
            self.assertNotIn("markdown", subset)

    def test_completed_specialist_result_validates_in_full_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            document = PDFiumBackend().extract_inventory(
                source,
                PaperWrightConfig(),
            ).document

            def fake_docling(_source, canonical, requests):
                bbox = requests[0]["scope"]["paperwright_bbox"]
                exported = {
                    "body": {"children": [{"$ref": "#/tables/0"}]},
                    "tables": [
                        {
                            "self_ref": "#/tables/0",
                            "label": "table",
                            "prov": [
                                {
                                    "page_no": requests[0]["scope"]["page_indices"][0]
                                    + 1,
                                    "bbox": {
                                        "l": bbox["x"],
                                        "t": bbox["y"],
                                        "r": bbox["x"] + bbox["width"],
                                        "b": bbox["y"] + bbox["height"],
                                        "coord_origin": "TOPLEFT",
                                    },
                                }
                            ],
                            "data": {"num_rows": 2, "num_cols": 2},
                        }
                    ],
                }
                return build_docling_evidence_from_documents(
                    [exported],
                    canonical,
                    requests,
                    provider_version="fixture",
                )

            with mock.patch(
                "paperwright.docling_provider.build_docling_evidence",
                side_effect=fake_docling,
            ):
                write_pdfium_source_evidence(
                    root / "source-evidence",
                    document,
                    source=source,
                )

            index = validate_source_evidence_bundle(root / "source-evidence")
            self.assertEqual(index["status"], "conflicted")
            self.assertEqual(index["summary"]["provider_count"], 4)
            requests = json.loads(
                (root / "source-evidence" / "specialist-requests.json").read_text(
                    encoding="utf-8"
                )
            )["requests"]
            self.assertEqual(requests[0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
