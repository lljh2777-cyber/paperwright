import hashlib
import unittest

from paper2md.backends.pdfium import _reading_order
from paper2md.models import BBox, Element, Page, PhysicalDocument, Provenance
from paper2md.writer import _markdown_text_groups, _title


def text(element_id, x, y, width, height, value):
    return Element(
        element_id=element_id,
        kind="text",
        page_index=0,
        bbox=BBox(x=x, y=y, width=width, height=height),
        text=value,
        source_object_id=None,
        provenance=Provenance(
            backend="fixture",
            method="project_authored_geometry",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
        metadata={"font_size": 1.0},
    )


class RealWorldTextRuleTests(unittest.TestCase):
    def test_fragmented_full_width_line_is_not_split_into_columns(self):
        items = [
            text("w1", 60, 100, 70, 16, "Modeling"),
            text("w2", 136, 100, 52, 16, "single"),
            text("w3", 194, 100, 36, 16, "cell"),
            text("w4", 238, 100, 80, 16, "trajectory"),
            text("w5", 326, 100, 50, 16, "using"),
            text("w6", 384, 100, 90, 16, "forward"),
        ]
        ordered = _reading_order(items, 612)
        self.assertEqual([item.element_id for item in ordered], [f"w{i}" for i in range(1, 7)])
        self.assertEqual({item.metadata["line_group"] for item in ordered}, {0})

    def test_true_two_columns_remain_column_major(self):
        items = [
            text("l1", 60, 100, 170, 12, "left one"),
            text("r1", 340, 100, 170, 12, "right one"),
            text("l2", 60, 125, 170, 12, "left two"),
            text("r2", 340, 125, 170, 12, "right two"),
        ]
        ordered = _reading_order(items, 612)
        self.assertEqual(
            [item.element_id for item in ordered],
            ["l1", "l2", "r1", "r2"],
        )

    def test_narrow_twenty_point_column_gutter_is_not_merged_as_one_line(self):
        items = [
            text("l1", 60, 100, 340, 12, "left column first line"),
            text("r1", 420, 100, 140, 12, "right column first line"),
            text("l2", 60, 125, 340, 12, "left column second line"),
            text("r2", 420, 125, 140, 12, "right column second line"),
        ]
        ordered = _reading_order(items, 612)
        self.assertEqual(
            [item.element_id for item in ordered],
            ["l1", "l2", "r1", "r2"],
        )
        self.assertNotEqual(
            ordered[0].metadata["line_group"],
            ordered[2].metadata["line_group"],
        )

    def test_missing_metadata_uses_multiline_geometric_title(self):
        items = _reading_order(
            [
                text("label", 360, 45, 90, 6, "TOOLS AND RESOURCES"),
                text("t1a", 170, 110, 60, 20, "Three-"),
                text("t1b", 232, 110, 180, 20, "dimensional single-"),
                text("t1c", 414, 110, 32, 20, "cell"),
                text("t2", 170, 136, 370, 20, "transcriptome imaging of thick tissues"),
                text("author", 170, 165, 120, 10, "Example Author"),
            ],
            612,
        )
        document = PhysicalDocument(
            source_sha256=hashlib.sha256(b"fixture").hexdigest(),
            backend="fixture",
            backend_version="1",
            pages=(Page(0, 612, 792, 0, tuple(items)),),
            metadata={"pdf_metadata": {}},
        )
        title, ids = _title(document)
        self.assertEqual(
            title,
            "Three-dimensional single-cell transcriptome imaging of thick tissues",
        )
        self.assertEqual(ids, {"t1a", "t1b", "t1c", "t2"})

    def test_generic_metadata_is_rejected_and_controls_are_removed_from_markdown(self):
        items = _reading_order(
            [
                text("t1", 44, 132, 440, 17, "Uniform Manifold Approximation"),
                text("t2", 44, 153, 490, 17, "and Projection in Microbiome Data"),
                text("body1", 44, 220, 90, 10, "Café"),
                text("body2", 140, 220, 90, 10, "\u0001analysis"),
            ],
            585,
        )
        document = PhysicalDocument(
            source_sha256=hashlib.sha256(b"fixture-2").hexdigest(),
            backend="fixture",
            backend_version="1",
            pages=(Page(0, 585, 783, 0, tuple(items)),),
            metadata={"pdf_metadata": {"Title": "SM-MSYS210386 1..6"}},
        )
        title, _ = _title(document)
        self.assertEqual(
            title,
            "Uniform Manifold Approximation and Projection in Microbiome Data",
        )
        groups = _markdown_text_groups(tuple(items))
        self.assertTrue(any(value == "Café analysis" for _, value in groups))


if __name__ == "__main__":
    unittest.main()
