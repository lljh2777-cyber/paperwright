import unittest

from paper2md.references import (
    ReferenceParagraph,
    detect_reference_section,
    is_reference_heading,
    removable_back_matter_keys,
    validate_reference_mode,
)


def _paragraph(index: int, text: str) -> ReferenceParagraph:
    return ReferenceParagraph(3, f"R{index:03d}", 0, text)


class ReferenceDetectionTests(unittest.TestCase):
    def test_heading_requires_following_reference_evidence(self):
        paragraphs = (
            _paragraph(1, "Discussion"),
            _paragraph(2, "References"),
            _paragraph(
                3,
                "1. Smith AB et al. Nature 2020; 10.1234/example.1",
            ),
            _paragraph(
                4,
                "2. Jones CD and Wang E. Science 2021; 12: 30-38.",
            ),
        )
        section = detect_reference_section(paragraphs)
        self.assertIsNotNone(section)
        assert section is not None
        self.assertEqual(section.start.text, "References")
        self.assertEqual(section.detection_method, "heading_and_entries")
        self.assertGreaterEqual(section.evidence_score, 5)

    def test_fragmented_heading_and_post_reference_boundary(self):
        paragraphs = (
            _paragraph(1, "Discussion"),
            _paragraph(2, "R E FE R E N CES AND NOTES"),
            _paragraph(
                3,
                "1. Smith AB et al. Nature 2020; doi: 10.1234/example.1",
            ),
            _paragraph(
                4,
                "2. Jones CD and Wang E. Science 2021; 12: 30-38.",
            ),
            _paragraph(5, "AC KNOWLE DGM E NTS"),
            _paragraph(6, "The authors thank the reviewers."),
        )
        section = detect_reference_section(paragraphs)
        self.assertIsNotNone(section)
        assert section is not None
        self.assertEqual(section.start_index, 1)
        self.assertEqual(section.end_index, 4)
        self.assertEqual(section.end.text, "AC KNOWLE DGM E NTS")

    def test_numbered_entry_run_without_heading(self):
        paragraphs = (
            _paragraph(1, "Received 26 September 2011."),
            _paragraph(
                2,
                "1. Smith AB et al. Nature 2020. "
                "2. Jones CD et al. Science 2021.",
            ),
            _paragraph(
                3,
                "3. Lee FG et al. Cell 2022. "
                "4. Wang HI et al. Nature 2023.",
            ),
            _paragraph(
                4,
                "Supplementary Information is linked online.",
            ),
        )
        section = detect_reference_section(paragraphs)
        self.assertIsNotNone(section)
        assert section is not None
        self.assertEqual(section.start_index, 1)
        self.assertEqual(section.end_index, 3)
        self.assertEqual(section.detection_method, "numbered_entry_run")

    def test_administrative_back_matter_is_removed_but_supplement_is_kept(self):
        paragraphs = (
            _paragraph(
                0,
                "Received 26 September 2011; accepted 27 March 2012. "
                "Published online 11 April 2012.",
            ),
            _paragraph(1, "Acknowledgments"),
            _paragraph(2, "The authors thank the reviewers."),
            _paragraph(3, "Author Contributions"),
            _paragraph(4, "A and B designed the study."),
            _paragraph(5, "Supplementary Information"),
            _paragraph(6, "Figures S1 to S4 are available online."),
            _paragraph(7, "Competing Interests"),
            _paragraph(8, "The authors declare no competing interests."),
        )
        keys = removable_back_matter_keys(
            paragraphs,
            1,
            reference_start_index=1,
        )
        self.assertEqual(
            keys,
            {
                paragraphs[0].key,
                paragraphs[1].key,
                paragraphs[2].key,
                paragraphs[3].key,
                paragraphs[4].key,
                paragraphs[7].key,
                paragraphs[8].key,
            },
        )

    def test_word_references_in_body_does_not_trigger_cutoff(self):
        paragraphs = (
            _paragraph(1, "The references discussed above support the model."),
            _paragraph(2, "References"),
            _paragraph(3, "No bibliography entries follow this heading."),
        )
        self.assertIsNone(detect_reference_section(paragraphs))

    def test_supported_headings_and_modes(self):
        self.assertTrue(is_reference_heading("REFERENCES"))
        self.assertTrue(is_reference_heading("R E FE R E N CES"))
        self.assertTrue(is_reference_heading("Literature Cited"))
        self.assertTrue(is_reference_heading("参考文献"))
        for mode in ("keep", "omit", "separate"):
            self.assertEqual(validate_reference_mode(mode), mode)
        with self.assertRaises(ValueError):
            validate_reference_mode("delete")


if __name__ == "__main__":
    unittest.main()
