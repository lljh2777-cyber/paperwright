import unittest

from paper2md.backends.pdfium import (
    _decorative_line_end_symbol_reason,
    _degenerate_bbox_reason,
    _degenerate_text_class,
)
from paper2md.models import BBox, Element, Provenance


class PDFiumDegenerateObjectTests(unittest.TestCase):
    def test_private_use_dingbat_requires_punctuated_preceding_text(self):
        provenance = Provenance("fixture", "native", "fixture")
        sentence = Element(
            "sentence",
            "text",
            0,
            BBox(60, 20, 30, 8),
            provenance,
            text="Finished.",
            metadata={"line_group": 2, "line_position": 0},
        )
        dingbat = Element(
            "dingbat",
            "text",
            0,
            BBox(92, 21, 5, 5),
            provenance,
            text="\uf0a3",
            metadata={
                "font_name": "Subset+Wingdings2",
                "line_group": 2,
                "line_position": 1,
            },
        )

        self.assertEqual(
            _decorative_line_end_symbol_reason(
                dingbat,
                [sentence, dingbat],
                100,
            ),
            "decorative_line_end_private_use_dingbat",
        )
        unpunctuated = Element(
            "sentence",
            "text",
            0,
            BBox(60, 20, 30, 8),
            provenance,
            text="Finished",
            metadata={"line_group": 2, "line_position": 0},
        )
        self.assertIsNone(
            _decorative_line_end_symbol_reason(
                dingbat,
                [unpunctuated, dingbat],
                100,
            )
        )

    def test_bbox_reason_distinguishes_lines_points_and_clipping(self):
        self.assertEqual(
            _degenerate_bbox_reason((5, 5, 5, 5), 100, 100),
            "zero_area",
        )
        self.assertEqual(
            _degenerate_bbox_reason((5, 5, 5, 10), 100, 100),
            "zero_width",
        )
        self.assertEqual(
            _degenerate_bbox_reason((5, 5, 10, 5), 100, 100),
            "zero_height",
        )
        self.assertEqual(
            _degenerate_bbox_reason((-10, 5, -1, 10), 100, 100),
            "outside_page",
        )

    def test_text_class_only_suppresses_provably_empty_content(self):
        self.assertEqual(
            _degenerate_text_class("", extraction_failed=False),
            "empty_text",
        )
        self.assertEqual(
            _degenerate_text_class(" \t", extraction_failed=False),
            "whitespace_text",
        )
        self.assertEqual(
            _degenerate_text_class("result", extraction_failed=False),
            "nonempty_text",
        )
        self.assertEqual(
            _degenerate_text_class(None, extraction_failed=True),
            "unreadable_text",
        )


if __name__ == "__main__":
    unittest.main()
