import json
import tempfile
import unittest
from pathlib import Path

from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.exceptions import ContractValidationError
from paperwright.source_evidence import (
    SOURCE_EVIDENCE_VERSION,
    build_pdfium_source_evidence,
    text_fingerprint,
    validate_source_evidence_bundle,
    write_pdfium_source_evidence,
)

from pdf_fixture_factory import create_born_digital_fixture


class SourceEvidenceTests(unittest.TestCase):
    def _document(self, root: Path):
        source = root / "fixture.pdf"
        create_born_digital_fixture(source)
        return PDFiumBackend().extract_inventory(
            source,
            PaperWrightConfig(),
        ).document

    def test_pdfium_snapshot_is_deterministic_and_lossless(self):
        with tempfile.TemporaryDirectory() as temp:
            document = self._document(Path(temp))

            first = build_pdfium_source_evidence(document)
            second = build_pdfium_source_evidence(document)

            self.assertEqual(first, second)
            index, artifacts = first
            snapshot = artifacts["providers/pdfium-native.json"]
            alignments = artifacts["alignments.json"]["alignments"]
            element_count = sum(len(page.elements) for page in document.pages)
            self.assertEqual(index["contract_version"], SOURCE_EVIDENCE_VERSION)
            self.assertEqual(snapshot["observation_count"], element_count)
            self.assertEqual(len(alignments), element_count)
            self.assertEqual(snapshot["status"], "complete")
            self.assertIn("object_inventory", snapshot["capabilities"])

            image = next(
                observation
                for page in snapshot["pages"]
                for observation in page["observations"]
                if observation["kind"] == "image"
            )
            self.assertEqual(image["materialization_status"], "deferred")
            self.assertIsNone(image["text_fingerprint"])
            page = snapshot["pages"][0]
            bbox = image["paperwright_bbox"]
            provider = image["provider_bbox"]
            self.assertAlmostEqual(provider["x0"], bbox["x"])
            self.assertAlmostEqual(provider["x1"], bbox["x"] + bbox["width"])
            self.assertAlmostEqual(
                provider["y0"],
                page["height"] - bbox["y"] - bbox["height"],
            )
            self.assertAlmostEqual(provider["y1"], page["height"] - bbox["y"])
            self.assertEqual(
                page["provider_to_paperwright_affine"],
                [1.0, 0.0, 0.0, -1.0, 0.0, page["height"]],
            )

            text = next(
                observation
                for page in snapshot["pages"]
                for observation in page["observations"]
                if observation["text"]
            )
            self.assertEqual(
                text["text_fingerprint"],
                text_fingerprint(text["text"]),
            )

    def test_written_bundle_validates_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = self._document(root)
            evidence_root = root / "source-evidence"

            record = write_pdfium_source_evidence(evidence_root, document)
            validated = validate_source_evidence_bundle(evidence_root)

            self.assertEqual(record["path"], "source-evidence/index.json")
            self.assertEqual(validated["source_sha256"], document.source_sha256)
            with self.assertRaisesRegex(ContractValidationError, "拒绝覆盖"):
                write_pdfium_source_evidence(evidence_root, document)

    def test_snapshot_tampering_is_rejected_by_hash_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = self._document(root)
            evidence_root = root / "source-evidence"
            write_pdfium_source_evidence(evidence_root, document)
            snapshot_path = evidence_root / "providers" / "pdfium-native.json"
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot["pages"][0]["observations"][0]["paperwright_bbox"]["x"] += 1
            snapshot_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ContractValidationError, "哈希不匹配"):
                validate_source_evidence_bundle(evidence_root)

    def test_pdfplumber_sidecar_adds_geometry_and_proposals_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            document = PDFiumBackend().extract_inventory(
                source,
                PaperWrightConfig(),
            ).document
            evidence_root = root / "source-evidence"

            write_pdfium_source_evidence(
                evidence_root,
                document,
                source=source,
            )
            index = validate_source_evidence_bundle(evidence_root)
            snapshot = json.loads(
                (
                    evidence_root
                    / "providers"
                    / "pdfplumber-geometry.json"
                ).read_text(encoding="utf-8")
            )
            claims = json.loads(
                (evidence_root / "claims.json").read_text(encoding="utf-8")
            )["claims"]
            kinds = {
                item["kind"]
                for page in snapshot["pages"]
                for item in page["observations"]
            }

            self.assertEqual(index["summary"]["provider_count"], 2)
            self.assertTrue({"char", "word", "line", "image"}.issubset(kinds))
            self.assertTrue(
                any(
                    item["kind"] == "image"
                    for item in snapshot["pages"][0]["observations"]
                )
            )
            self.assertTrue(claims)
            self.assertTrue(
                all(
                    claim["claim_type"] == "table_region"
                    and claim["status"] == "proposed"
                    and claim["payload"]["direct_markdown_authority"] is False
                    for claim in claims
                )
            )


if __name__ == "__main__":
    unittest.main()
