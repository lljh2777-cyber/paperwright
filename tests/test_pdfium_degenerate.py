import unittest

from paper2md.backends.pdfium import (
    _degenerate_bbox_reason,
    _degenerate_text_class,
)


class PDFiumDegenerateObjectTests(unittest.TestCase):
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
