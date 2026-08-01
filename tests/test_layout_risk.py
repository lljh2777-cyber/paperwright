import tempfile
import unittest
from pathlib import Path

from paper2md.backends.pdfium import PDFiumBackend
from paper2md.config import Paper2MDConfig
from paper2md.layout_models import (
    LayoutCandidate,
    LayoutPage,
    LayoutTask,
    NormalizedBBox,
    RASTER_LAYOUT_TASK_VERSION,
)
from paper2md.layout_risk import assess_layout_risk
from paper2md.models import BBox, Element, Page, PhysicalDocument, Provenance

from pdf_fixture_factory import create_born_digital_fixture


class LayoutRiskTests(unittest.TestCase):
    def test_raster_ambiguity_selects_only_the_risky_page(self):
        provenance = Provenance("fixture", "native", "fixture")
        pages = tuple(
            Page(
                page_index=index,
                width=100,
                height=100,
                rotation=0,
                elements=(
                    Element(
                        f"p{index}-text",
                        "text",
                        index,
                        BBox(10, 10, 80, 10),
                        provenance,
                        text="body",
                    ),
                ),
            )
            for index in range(2)
        )
        document = PhysicalDocument(
            source_sha256="a" * 64,
            backend="fixture",
            backend_version="1",
            pages=pages,
        )

        def task(page_index: int, count: int) -> LayoutTask:
            candidates = tuple(
                LayoutCandidate(
                    candidate_id=f"C{item + 1:03d}",
                    bbox=NormalizedBBox(0.1, 0.1, 0.2, 0.2),
                    element_kinds=("raster",),
                )
                for item in range(count)
            )
            return LayoutTask(
                source_sha256=document.source_sha256,
                page=LayoutPage.from_page(pages[page_index]),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=candidates,
                contract_version=RASTER_LAYOUT_TASK_VERSION,
            )

        assessment = assess_layout_risk(
            (task(0, 2), task(1, 9)),
            document,
        )

        self.assertEqual(assessment.escalation_page_indices, (1,))
        self.assertEqual(
            assessment.pages[1].reasons,
            ("raster_region_ambiguity_high",),
        )
        self.assertEqual(
            assessment.policy_version,
            "paper2md-layout-risk-v0.2",
        )

    def test_independent_moderate_signals_combine_into_upgrade(self):
        provenance = Provenance("fixture", "native", "fixture")
        page = Page(
            page_index=0,
            width=100,
            height=100,
            rotation=0,
            elements=(
                Element(
                    "p0-text",
                    "text",
                    0,
                    BBox(10, 10, 80, 10),
                    provenance,
                    text="body",
                ),
            ),
        )
        document = PhysicalDocument(
            source_sha256="b" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        candidates = tuple(
            LayoutCandidate(
                candidate_id=f"C{index + 1:03d}",
                bbox=NormalizedBBox(0.1, 0.1, 0.2, 0.2),
                element_kinds=(
                    ("text", "raster")
                    if index < 3
                    else (("raster",) if index == 3 else ("text",))
                ),
            )
            for index in range(16)
        )
        task = LayoutTask(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(page),
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=candidates,
            contract_version=RASTER_LAYOUT_TASK_VERSION,
        )

        assessment = assess_layout_risk((task,), document)
        risk = assessment.pages[0]

        self.assertEqual(risk.reasons, ("combined_layout_ambiguity",))
        self.assertEqual(risk.risk_score, 3)
        self.assertEqual(
            risk.signals,
            (
                "candidate_fragmentation_elevated",
                "raster_region_ambiguity_elevated",
                "mixed_content_ambiguity_elevated",
            ),
        )
        self.assertEqual(risk.metrics["mixed_candidate_count"], 3)
        serialized = assessment.to_dict()
        self.assertEqual(
            serialized["policy"]["composite_score_threshold"],
            3,
        )
        self.assertEqual(serialized["pages"][0]["risk_score"], 3)

    def test_one_moderate_signal_does_not_force_upgrade(self):
        provenance = Provenance("fixture", "native", "fixture")
        page = Page(
            page_index=0,
            width=100,
            height=100,
            rotation=0,
            elements=(
                Element(
                    "p0-text",
                    "text",
                    0,
                    BBox(10, 10, 80, 10),
                    provenance,
                    text="body",
                ),
            ),
        )
        document = PhysicalDocument(
            source_sha256="c" * 64,
            backend="fixture",
            backend_version="1",
            pages=(page,),
        )
        task = LayoutTask(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(page),
            candidate_generator_version="fixture",
            feature_schema_version="fixture",
            candidates=tuple(
                LayoutCandidate(
                    candidate_id=f"C{index + 1:03d}",
                    bbox=NormalizedBBox(0.1, 0.1, 0.2, 0.2),
                    element_kinds=("text",),
                )
                for index in range(16)
            ),
            contract_version=RASTER_LAYOUT_TASK_VERSION,
        )

        risk = assess_layout_risk((task,), document).pages[0]

        self.assertFalse(risk.requires_full_object_analysis)
        self.assertEqual(risk.risk_score, 1)
        self.assertEqual(
            risk.signals,
            ("candidate_fragmentation_elevated",),
        )

    def test_pdfium_hybrid_walks_only_selected_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fixture.pdf"
            create_born_digital_fixture(source)
            backend = PDFiumBackend()

            result = backend.extract_hybrid(
                source,
                Paper2MDConfig(),
                full_page_indices=(0,),
            )

            self.assertEqual(
                result.document.metadata["extraction_profile"],
                "hybrid-standard",
            )
            self.assertEqual(result.performance["extraction_mode"], "hybrid")
            self.assertEqual(
                [item["extraction_mode"] for item in result.performance["pages"]],
                ["full", "text-only"],
            )
            self.assertTrue(
                any(
                    element.kind in {"image", "vector"}
                    for element in result.document.pages[0].elements
                )
            )
            self.assertTrue(
                all(
                    element.kind == "text"
                    for element in result.document.pages[1].elements
                )
            )


if __name__ == "__main__":
    unittest.main()
