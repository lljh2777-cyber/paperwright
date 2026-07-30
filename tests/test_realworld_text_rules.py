import hashlib
import unittest
from dataclasses import replace

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
    def test_iterative_merge_restores_native_contiguous_dixon_line(self):
        values = [
            text("p0000-text-00073", 37.21, 593.60, 61.28, 6.70, "and fluorescence"),
            text("p0000-text-00074", 102.12, 594.27, 22.75, 6.03, "in situ"),
            text(
                "p0000-text-00075",
                128.18,
                593.60,
                103.64,
                8.97,
                "hybridization (FISH) results",
            ),
            text("p0000-text-00076", 232.16, 593.38, 8.25, 3.42, "4–6"),
            text("p0000-text-00077", 241.14, 594.05, 47.71, 6.29, ". Our IMR90"),
            text(
                "p0000-text-00078",
                37.20,
                604.27,
                251.49,
                8.97,
                "Hi-C data show a high degree of similarity",
            ),
        ]
        values = [
            replace(
                item,
                metadata={
                    **item.metadata,
                    "native_order": int(item.element_id.rsplit("-", 1)[1]),
                },
            )
            for item in values
        ]
        ordered = _reading_order(values, 595)
        first_line = [
            item
            for item in ordered
            if item.metadata["line_group"] == ordered[0].metadata["line_group"]
        ]
        self.assertEqual(
            [item.element_id for item in first_line],
            [f"p0000-text-{index:05d}" for index in range(73, 78)],
        )
        self.assertNotEqual(
            ordered[0].metadata["line_group"],
            ordered[-1].metadata["line_group"],
        )

    def test_native_union_line_text_wins_over_overlapping_object_text(self):
        items = _reading_order(
            [
                text("a", 37, 100, 190, 7.4, "limiting a unifi"),
                text("b", 226.5, 100, 60, 7.4, "fied view"),
            ],
            594,
        )
        items = [
            replace(
                item,
                metadata={
                    **item.metadata,
                    "native_line_text": "limiting a unified view",
                },
            )
            for item in items
        ]
        self.assertEqual(
            _markdown_text_groups(tuple(items))[0][1],
            "limiting a unified view",
        )

    def test_control_glyph_does_not_split_one_visual_line(self):
        items = [
            text("a", 37.2, 471.0, 86.2, 7.4, "prognosis. Cross-cohor"),
            text("b", 123.7, 471.0, 157.4, 7.4, "t analyses, validations"),
            text("soft", 281.6, 474.5, 2.0, 0.7, "\u0002"),
        ]
        ordered = _reading_order(items, 594)
        self.assertEqual({item.metadata["line_group"] for item in ordered}, {0})
        self.assertEqual(
            _markdown_text_groups(tuple(ordered))[0][1],
            "prognosis. Cross-cohort analyses, validations",
        )

    def test_lines_are_reconstructed_into_paragraph_and_soft_break_word(self):
        ordered = _reading_order(
            [
                text("l1", 37, 155, 240, 7.4, "INTRODUCTION: A spatial"),
                text("l2", 37, 165.5, 238, 7.4, "atlas spans several"),
                text("l3", 37, 176, 230, 7.4, "cancer types and artificial intelli"),
                text("soft", 267.5, 179.5, 2, 0.7, "\u0002"),
                text("l4", 37, 186.5, 210, 7.4, "gence methods."),
                text("heading", 37, 207.5, 48, 6.2, "RESULTS:"),
            ],
            594,
        )
        groups = _markdown_text_groups(tuple(ordered))
        self.assertEqual(
            groups[0][1],
            "INTRODUCTION: A spatial atlas spans several cancer types and "
            "artificial intelligence methods.",
        )
        self.assertEqual(groups[1][1], "RESULTS:")

    def test_compact_native_fragments_do_not_gain_spaces(self):
        items = _reading_order(
            [
                text("a", 37, 100, 45.4, 7.4, "context-"),
                text("b", 82.8, 100, 4.4, 6.1, "d"),
                text("c", 87.6, 100, 80, 7.4, "ependent"),
            ],
            594,
        )
        self.assertEqual(
            _markdown_text_groups(tuple(items))[0][1],
            "context-dependent",
        )

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
