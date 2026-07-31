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

    def test_tight_same_font_fragments_are_joined_from_geometry(self):
        result = join_line_elements(
            [
                text("left", 10, 10, 40, "adj", font_name="Body-Roman"),
                text("right", 50.95, 10, 40, "acent", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "adjacent")
        self.assertIn(
            "collapsed_tight_same_font_fragment_gap",
            {event.code for event in result.events},
        )

    def test_italic_scientific_token_followed_by_roman_prose_gets_space(self):
        result = join_line_elements(
            [
                text("gene", 10, 10, 20, "IL10", font_name="Body-Italic"),
                text("prose", 30.1, 10, 20, "and", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "IL10 and")
        self.assertIn(
            "inserted_geometric_word_space",
            {event.code for event in result.events},
        )

    def test_italic_overhang_does_not_hide_scientific_word_boundary(self):
        result = join_line_elements(
            [
                text("gene", 10, 10, 20, "MKI67", font_name="Body-Italic"),
                text("prose", 29.4, 10, 20, "and", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "MKI67 and")

    def test_math_font_greek_symbol_followed_by_prose_gets_space(self):
        result = join_line_elements(
            [
                text("greek", 10, 10, 4, "α", font_name="Math-Regular"),
                text("prose", 14.15, 10, 30, "responses", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "α responses")

    def test_micro_unit_symbol_remains_compact(self):
        result = join_line_elements(
            [
                text("number", 10, 10, 5, "2 ", font_name="Body-Roman"),
                text("micro", 16.0, 10, 4, "μ", font_name="Math-Regular"),
                text("unit", 20.1, 10, 20, "g/ml", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "2 μg/ml")

    def test_acronym_followed_by_prose_boundary_word_gets_space(self):
        result = join_line_elements(
            [
                text("prefix", 10, 10, 40, "across TCG", font_name="Body-Roman"),
                text("suffix", 50.5, 10, 5, "A", font_name="Body-Roman"),
                text("prose", 55.45, 10, 40, "and cohorts", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "across TCGA and cohorts")

    def test_percent_followed_by_word_gets_space(self):
        result = join_line_elements(
            [
                text("number", 10, 10, 20, "80", font_name="Body-Roman"),
                text("percent", 30.1, 10, 4, "%", font_name="Math-Regular"),
                text("word", 34.5, 10, 30, "training", font_name="Body-Roman"),
            ]
        )
        self.assertEqual(result.text, "80% training")
        self.assertIn(
            "inserted_geometric_word_space",
            {event.code for event in result.events},
        )

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
