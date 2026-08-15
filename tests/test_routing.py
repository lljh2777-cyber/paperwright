from __future__ import annotations

import unittest

from paperwright.layout_models import (
    LayoutCandidate,
    LayoutPage,
    LayoutSeparator,
    LayoutTask,
    NormalizedBBox,
)
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.routing import (
    ROUTE_HUMAN_REVIEW,
    ROUTE_L0_RULE,
    ROUTE_L1_TEXT_MODEL,
    ROUTE_L2_VISUAL_MODEL,
    plan_routing,
)


def _element(element_id, text, y, page_index=0, x=100.0):
    return Element(
        element_id=element_id,
        kind="text",
        page_index=page_index,
        bbox=BBox(x, y, 300, 12),
        text=text,
        provenance=Provenance(
            backend="fixture",
            method="self-generated",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
    )


def _task(page_index, source_sha256, candidate_count=0, separator_count=0):
    candidates = tuple(
        LayoutCandidate(
            candidate_id=f"C{index:03d}",
            bbox=NormalizedBBox(0.1, 0.1 + index * 0.02, 0.4, 0.05),
            source_element_ids=(),
            element_kinds=("text",),
            features={},
        )
        for index in range(candidate_count)
    )
    separators = tuple(
        LayoutSeparator(
            separator_id=f"S{index:03d}",
            orientation="horizontal" if index % 2 == 0 else "vertical",
            bbox=NormalizedBBox(0.1, 0.1 + index * 0.01, 0.4, 0.01),
            adjacent_candidate_ids=(
                f"C{index % max(candidate_count, 1):03d}",
                f"C{(index + 1) % max(candidate_count, 1):03d}",
            ),
            features={},
        )
        for index in range(separator_count)
    )
    return LayoutTask(
        source_sha256=source_sha256,
        page=LayoutPage(
            page_index=page_index,
            width=600,
            height=800,
            rotation=0,
            coordinate_system="top-left/pdf-point/y-down",
        ),
        candidate_generator_version="fixture-v1",
        feature_schema_version="fixture-v1",
        candidates=candidates,
        separators=separators,
        metadata={},
    )


class RoutingTests(unittest.TestCase):
    SHA = "a" * 64

    def _document_and_tasks(self):
        source = self.SHA
        page0 = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("a", "Ordinary paragraph text is long enough", 100),
                _element("b", "Another ordinary paragraph line", 130),
            ),
        )
        page1 = Page(
            page_index=1,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("c", "The first paragraph ends here", 100, page_index=1),
                _element("d", "and continues on the next line", 130, page_index=1),
                _element("e", "and also continues once more", 160, page_index=1),
            ),
        )
        page2 = Page(
            page_index=2,
            width=600,
            height=800,
            rotation=0,
            elements=(
                _element("f", "Ordinary text on a complex page", 100, page_index=2),
            ),
        )
        document = PhysicalDocument(
            source_sha256=source,
            backend="fixture",
            backend_version="1",
            pages=(page0, page1, page2),
            metadata={"title": "Fixture"},
        )
        tasks = (
            _task(0, source),
            _task(1, source),
            _task(2, source, candidate_count=8, separator_count=20),
        )
        return document, tasks

    def test_routes_simple_lowercase_and_complex_pages(self):
        document, tasks = self._document_and_tasks()
        plan = plan_routing(document, tasks)
        routes = {page.page_index: page.route for page in plan.pages}
        self.assertEqual(routes[0], ROUTE_L0_RULE)
        self.assertEqual(routes[1], ROUTE_L1_TEXT_MODEL)
        self.assertEqual(routes[2], ROUTE_L2_VISUAL_MODEL)
        self.assertIn(
            "lowercase_continuation_fragments",
            plan.pages[1].reasons,
        )
        self.assertIn(
            "complex_layout_geometry",
            plan.pages[2].reasons,
        )
        self.assertEqual(
            plan.canonical_json(),
            plan.canonical_json(),
        )

    def test_native_text_missing_routes_to_human(self):
        page = Page(
            page_index=0,
            width=600,
            height=800,
            rotation=0,
            elements=(),
        )
        document = PhysicalDocument(
            source_sha256=self.SHA,
            backend="fixture",
            backend_version="1",
            pages=(page,),
            metadata={"title": "Fixture"},
        )
        tasks = (_task(0, self.SHA),)
        plan = plan_routing(document, tasks)
        self.assertEqual(plan.pages[0].route, ROUTE_HUMAN_REVIEW)
        self.assertIn("native_text_missing", plan.pages[0].reasons)

    def test_off_mode_forces_rule_route(self):
        document, tasks = self._document_and_tasks()
        plan = plan_routing(document, tasks, mode="off")
        self.assertTrue(
            all(page.route == ROUTE_L0_RULE for page in plan.pages)
        )


if __name__ == "__main__":
    unittest.main()
