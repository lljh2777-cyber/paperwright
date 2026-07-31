import unittest

from paper2md.models import BBox, Element, Provenance
from paper2md.text_reconstruction import (
    clean_text,
    join_line_elements,
    reconstruct_text_groups,
)


def text(
    element_id: str,
    x: float,
    y: float,
    width: float,
    value: str,
    *,
    line_group: int = 0,
    font_name: str = "BentonSans-Bold",
) -> Element:
    return Element(
        element_id=element_id,
        kind="text",
        page_index=0,
        bbox=BBox(x, y, width, 6.0),
        provenance=Provenance(
            backend="fixture",
            method="project_authored_geometry",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
        text=value,
        metadata={
            "font_name": font_name,
            "font_size": 6.0,
            "line_group": line_group,
        },
    )


class TextReconstructionTests(unittest.TestCase):
    def test_geometric_letter_spacing_is_collapsed(self):
        elements = [
            text("ca", 37.328, 40.468, 11.408, "CA"),
            text("n", 50.080, 40.580, 5.000, "N"),
            text("ce", 56.616, 40.468, 10.888, "CE"),
            text("r", 68.992, 40.580, 4.640, "R"),
        ]
        result = join_line_elements(elements)
        self.assertEqual(result.text, "CANCER")
        self.assertEqual(
            [item.code for item in result.events],
            ["collapsed_geometric_letter_spacing"],
        )
        self.assertEqual(result.events[0].before, "CA N CE R")

    def test_true_word_gap_splits_two_letter_spaced_words(self):
        elements = [
            text("supple", 10, 10, 20, "SUPPLE"),
            text("m1", 31.0, 10, 3, "M"),
            text("e", 35.0, 10, 3, "E"),
            text("ntary", 39.0, 10, 18, "NTARY"),
            text("mate", 61.0, 10, 16, "MATE"),
            text("r", 78.0, 10, 3, "R"),
            text("ials", 82.0, 10, 14, "IALS"),
        ]
        result = join_line_elements(elements)
        self.assertEqual(result.text, "SUPPLEMENTARY MATERIALS")
        self.assertEqual(len(result.events), 2)

    def test_panel_labels_and_two_acronyms_are_not_collapsed(self):
        panels = [
            text("a", 10, 10, 3, "A"),
            text("b", 25, 10, 3, "B"),
            text("c", 40, 10, 3, "C"),
        ]
        acronyms = [
            text("dna", 10, 20, 12, "DNA"),
            text("rna", 23.2, 20, 12, "RNA"),
        ]
        self.assertEqual(join_line_elements(panels).text, "A B C")
        self.assertEqual(join_line_elements(acronyms).text, "DNA RNA")

    def test_missing_font_evidence_does_not_collapse_fragments(self):
        elements = [
            text("ca", 10, 10, 8, "CA", font_name=""),
            text("n", 19, 10, 3, "N", font_name=""),
            text("cer", 23, 10, 10, "CER", font_name=""),
        ]
        self.assertEqual(join_line_elements(elements).text, "CA N CER")

    def test_deduplicated_paint_objects_remain_in_provenance(self):
        elements = [
            text("paint-1", 10, 10, 40, "Result"),
            text("paint-2", 10.1, 10.1, 39.9, "Result"),
        ]
        result = join_line_elements(elements)
        self.assertEqual(result.text, "Result")
        self.assertEqual(result.element_ids, ("paint-1", "paint-2"))

    def test_visible_hyphen_is_preserved_across_line_boundary(self):
        elements = (
            text("line-1", 10, 10, 50, "high-", line_group=0),
            text("line-2", 10, 17, 60, "throughput method", line_group=1),
        )
        result = reconstruct_text_groups(elements)[0]
        self.assertEqual(result.text, "high-throughput method")
        self.assertIn(
            "joined_line_after_visible_hyphen",
            [item.code for item in result.events],
        )

    def test_unicode_controls_and_ligatures_are_normalised(self):
        value, removed = clean_text("of\ufb01ce\u0002 methods\u00a0work")
        self.assertEqual(value, "office methods work")
        self.assertEqual(removed, 1)

    def test_suspicious_unicode_is_reported_without_guessing(self):
        result = join_line_elements(
            [text("bad", 10, 10, 60, "result \ufffd remains")]
        )
        self.assertEqual(result.text, "result \ufffd remains")
        self.assertEqual(result.warnings[0].code, "suspicious_unicode_codepoint")
        self.assertEqual(result.warnings[0].codepoints, ("U+FFFD",))


if __name__ == "__main__":
    unittest.main()
