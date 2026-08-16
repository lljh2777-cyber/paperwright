from __future__ import annotations

import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.completeness import build_completeness_report
from paperwright.issue_routing import (
    ISSUE_CAPTION_VISUAL_BINDING,
    ISSUE_LAYOUT_GEOMETRY_AMBIGUITY,
    ISSUE_PAGE_VISUAL_PRESERVATION,
    ISSUE_PARAGRAPH_CONTINUATION,
    IssueRoutingPlan,
    plan_issue_routing,
    refine_issue_routing,
    refine_issue_routing_with_text_task,
    validate_issue_routing,
)
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


def _task(
    page_index,
    source_sha256,
    candidate_count=0,
    separator_count=0,
    metadata=None,
):
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
        metadata=metadata or {},
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
                _element(
                    "f-long",
                    "and continues on a substantially longer extracted line without punctuation",
                    190,
                    page_index=1,
                ),
                _element(
                    "g-long",
                    "and also continues once more across another substantially long block",
                    220,
                    page_index=1,
                ),
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

    def test_issue_routing_keeps_l0_base_and_escalates_only_issues(self):
        document, tasks = self._document_and_tasks()
        plan = plan_issue_routing(document, tasks)
        value = plan.to_dict()
        validate_issue_routing(value)
        self.assertTrue(all(page["base_route"] == ROUTE_L0_RULE for page in value["pages"]))
        by_kind = {issue["kind"]: issue for issue in value["issues"]}
        self.assertNotIn(ISSUE_PARAGRAPH_CONTINUATION, by_kind)
        self.assertEqual(
            by_kind[ISSUE_LAYOUT_GEOMETRY_AMBIGUITY]["route"],
            ROUTE_L2_VISUAL_MODEL,
        )
        self.assertEqual(value["pages"][0]["compatibility_route"], ROUTE_L0_RULE)
        self.assertEqual(value["pages"][1]["compatibility_route"], ROUTE_L0_RULE)
        self.assertEqual(value["pages"][2]["compatibility_route"], ROUTE_L2_VISUAL_MODEL)
        self.assertEqual(plan.canonical_json(), plan.canonical_json())

    def test_text_issues_are_discovered_from_validator_eligible_blocks(self):
        from paperwright.text_review import build_text_task
        from tests.test_text_review import JoinBlocksContractTests

        model = JoinBlocksContractTests()._model()
        initial = IssueRoutingPlan(
            source_sha256=model["source_sha256"],
            page_count=1,
            issues=(),
        ).to_dict()
        task = build_text_task(model)
        refined = refine_issue_routing_with_text_task(
            initial,
            task,
            model,
        ).to_dict()
        text_issues = [
            item
            for item in refined["issues"]
            if item["kind"] == ISSUE_PARAGRAPH_CONTINUATION
        ]
        self.assertEqual(len(text_issues), 2)
        self.assertTrue(
            all(item["route"] == ROUTE_L1_TEXT_MODEL for item in text_issues)
        )
        self.assertTrue(
            all(len(item["scope"]["candidate_ids"]) == 2 for item in text_issues)
        )

    def test_non_text_page_is_a_deterministic_preservation_issue(self):
        vector = Element(
            element_id="v1",
            kind="vector",
            page_index=0,
            bbox=BBox(20, 20, 300, 400),
            provenance=Provenance(
                backend="fixture",
                method="self-generated",
                source_ref="fixture:v1",
                confidence=1.0,
            ),
        )
        document = PhysicalDocument(
            source_sha256=self.SHA,
            backend="fixture",
            backend_version="1",
            pages=(Page(0, 600, 800, 0, (vector,)),),
        )
        plan = plan_issue_routing(document, (_task(0, self.SHA),)).to_dict()
        self.assertEqual(len(plan["issues"]), 1)
        issue = plan["issues"][0]
        self.assertEqual(issue["kind"], ISSUE_PAGE_VISUAL_PRESERVATION)
        self.assertEqual(issue["route"], ROUTE_L0_RULE)
        self.assertEqual(issue["fallback_route"], ROUTE_HUMAN_REVIEW)

    def test_scientific_caption_binding_is_a_local_visual_issue(self):
        caption = _element(
            "caption",
            "Fig. 2 | A scientific result.",
            600,
        )
        image = Element(
            element_id="image",
            kind="image",
            page_index=0,
            bbox=BBox(80, 180, 420, 360),
            provenance=Provenance(
                backend="fixture",
                method="self-generated",
                source_ref="fixture:image",
                confidence=1.0,
            ),
        )
        document = PhysicalDocument(
            source_sha256=self.SHA,
            backend="fixture",
            backend_version="1",
            pages=(Page(0, 600, 800, 0, (image, caption)),),
        )
        task = _task(
            0,
            self.SHA,
            metadata={"raster_evidence": {"region_count": 1}},
        )
        plan = plan_issue_routing(document, (task,)).to_dict()
        issues = [
            item
            for item in plan["issues"]
            if item["kind"] == ISSUE_CAPTION_VISUAL_BINDING
        ]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["route"], ROUTE_L2_VISUAL_MODEL)
        self.assertEqual(issues[0]["scope"]["element_ids"], ["caption"])

    def test_raster_confirmed_blank_page_has_no_issue(self):
        document = PhysicalDocument(
            source_sha256=self.SHA,
            backend="fixture",
            backend_version="1",
            pages=(Page(0, 600, 800, 0, ()),),
        )
        task = _task(
            0,
            self.SHA,
            metadata={"raster_evidence": {"region_count": 0}},
        )
        value = plan_issue_routing(document, (task,)).to_dict()
        self.assertEqual(value["issues"], [])
        self.assertEqual(value["pages"][0]["compatibility_route"], ROUTE_L0_RULE)

    def test_issue_routing_rejects_page_reference_tampering(self):
        document, tasks = self._document_and_tasks()
        value = plan_issue_routing(document, tasks).to_dict()
        value["pages"][2]["issue_ids"] = []
        with self.assertRaisesRegex(ContractValidationError, "不一致|守恒"):
            validate_issue_routing(value)

    def test_completeness_findings_feed_back_as_local_visual_issue(self):
        document, tasks = self._document_and_tasks()
        initial = plan_issue_routing(document, tasks).to_dict()
        report = build_completeness_report(
            document,
            projected_text_counts={0: 1, 1: 1, 2: 1},
            projected_visual_counts={},
            orphan_caption_counts={0: 1},
        )
        refined = refine_issue_routing(initial, report).to_dict()
        added = [
            item
            for item in refined["issues"]
            if item["kind"] == "caption_visual_binding"
            and item["page_index"] == 0
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["route"], ROUTE_L2_VISUAL_MODEL)
        self.assertIn(
            "completeness_finding:caption_without_bound_visual",
            added[0]["signals"],
        )


if __name__ == "__main__":
    unittest.main()
