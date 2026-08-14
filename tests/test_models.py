import json
import math
import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance

from helpers import minimal_document


class PhysicalDocumentTests(unittest.TestCase):
    def test_fixture_round_trip(self):
        original = minimal_document()
        restored = PhysicalDocument.from_dict(json.loads(original.canonical_json()))
        self.assertEqual(restored, original)
        self.assertEqual(restored.pages[0].elements[0].text, "Café α bootstrap")

    def test_canonical_serialization_is_byte_deterministic(self):
        first = minimal_document()
        second = minimal_document()
        self.assertEqual(first.canonical_json().encode(), second.canonical_json().encode())
        self.assertEqual(first.deterministic_sha256(), second.deterministic_sha256())

    def test_bbox_rejects_zero_area(self):
        with self.assertRaisesRegex(ContractValidationError, "正面积"):
            BBox(0, 0, 0, 1)

    def test_bbox_rejects_nan(self):
        with self.assertRaisesRegex(ContractValidationError, "有限数"):
            BBox(math.nan, 0, 1, 1)

    def test_page_rejects_out_of_bounds_bbox(self):
        element = Element(
            "e1",
            "text",
            0,
            BBox(95, 1, 10, 10),
            Provenance("fixture", "test", "fixture:e1"),
            text="x",
        )
        with self.assertRaisesRegex(ContractValidationError, "右边界"):
            Page(0, 100, 100, 0, (element,))

    def test_document_rejects_duplicate_element_id(self):
        provenance = Provenance("fixture", "test", "fixture:e")
        element = Element("same", "text", 0, BBox(1, 1, 2, 2), provenance, text="a")
        element2 = Element("same", "text", 1, BBox(1, 1, 2, 2), provenance, text="b")
        with self.assertRaisesRegex(ContractValidationError, "唯一"):
            PhysicalDocument(
                source_sha256="a" * 64,
                backend="fixture",
                backend_version="1",
                pages=(
                    Page(0, 100, 100, 0, (element,)),
                    Page(1, 100, 100, 0, (element2,)),
                ),
            )

    def test_document_rejects_non_contiguous_pages(self):
        with self.assertRaisesRegex(ContractValidationError, "连续"):
            PhysicalDocument(
                source_sha256="b" * 64,
                backend="fixture",
                backend_version="1",
                pages=(Page(1, 100, 100, 0),),
            )

    def test_provenance_required(self):
        with self.assertRaisesRegex(ContractValidationError, "必填"):
            Provenance("", "method", "ref")


if __name__ == "__main__":
    unittest.main()
