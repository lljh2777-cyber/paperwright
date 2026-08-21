import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from paperwright.exceptions import ContractValidationError
from paperwright.layout_models import FinalLayout, LayoutPage, LayoutRegion, NormalizedBBox
from paperwright.models import BBox, Element, Page, PhysicalDocument, Provenance
from paperwright.paper_recipe import (
    build_paper_recipe,
    canonical_article_tree_json,
    canonical_paper_recipe_json,
    compile_article_tree,
    refine_layouts_with_recipe,
    validate_article_tree,
    validate_paper_recipe,
)
from paperwright.source_evidence import write_pdfium_source_evidence


def _element(
    element_id: str,
    kind: str,
    page_index: int,
    bbox: BBox,
    text: str | None = None,
) -> Element:
    return Element(
        element_id=element_id,
        kind=kind,
        page_index=page_index,
        bbox=bbox,
        text=text,
        provenance=Provenance(
            backend="fixture",
            method="fixture",
            source_ref=f"fixture:{element_id}",
            confidence=1.0,
        ),
    )


def _document() -> PhysicalDocument:
    return PhysicalDocument(
        source_sha256=hashlib.sha256(b"paper-recipe-fixture").hexdigest(),
        backend="fixture",
        backend_version="1",
        metadata={"native_object_inventory": "pdfium_page_objects_v0.1"},
        pages=(
            Page(
                page_index=0,
                width=600,
                height=800,
                rotation=0,
                elements=(
                    _element("p0-title", "text", 0, BBox(70, 50, 300, 30), "Paper title"),
                    _element("p0-logo", "image", 0, BBox(70, 100, 80, 40)),
                    _element("p0-sidebar", "text", 0, BBox(70, 160, 350, 45), "Citation and license text"),
                    _element("p0-license-vector", "vector", 0, BBox(20, 700, 40, 40)),
                ),
            ),
            Page(
                page_index=1,
                width=600,
                height=800,
                rotation=0,
                elements=(
                    _element("p1-caption", "text", 1, BBox(70, 80, 300, 20), "Figure 1. Result"),
                    _element("p1-figure", "image", 1, BBox(70, 110, 360, 260)),
                    _element("p1-body", "text", 1, BBox(70, 400, 400, 30), "Native body text"),
                ),
            ),
        ),
    )


class PaperRecipeTests(unittest.TestCase):
    def test_recipe_and_tree_are_deterministic_and_text_conserving(self):
        document = _document()
        with tempfile.TemporaryDirectory() as temp:
            evidence_root = Path(temp) / "source-evidence"
            write_pdfium_source_evidence(evidence_root, document)
            first = build_paper_recipe(document, evidence_root)
            second = build_paper_recipe(document, evidence_root)
            self.assertEqual(
                canonical_paper_recipe_json(first),
                canonical_paper_recipe_json(second),
            )
            validate_paper_recipe(first, document=document, evidence_root=evidence_root)

            by_reason = {item["reason"]: item for item in first["actions"]}
            self.assertEqual(
                by_reason["small_uncaptioned_first_page_native_image"]["element_ids"],
                ["p0-logo"],
            )
            self.assertEqual(
                by_reason["native_image_object_preservation"]["element_ids"],
                ["p1-figure"],
            )
            self.assertEqual(
                by_reason["native_explicit_figure_or_table_caption"]["element_ids"],
                ["p1-caption"],
            )
            self.assertEqual(
                by_reason["first_page_bottom_publication_vector_furniture"][
                    "element_ids"
                ],
                ["p0-license-vector"],
            )

            tree = compile_article_tree(document, first)
            validate_article_tree(tree, document=document, recipe=first)
            self.assertEqual(tree["summary"]["generated_text_count"], 0)
            leaves = [
                item for item in tree["nodes"] if item["kind"] == "source_element"
            ]
            self.assertEqual(len(leaves), 7)
            self.assertFalse(any("text" in item for item in tree["nodes"]))
            self.assertEqual(
                canonical_article_tree_json(tree),
                canonical_article_tree_json(compile_article_tree(document, first)),
            )
            tampered_tree = json.loads(canonical_article_tree_json(tree))
            next(
                item
                for item in tampered_tree["nodes"]
                if item["source_text_sha256"] is not None
            )["source_text_sha256"] = "0" * 64
            with self.assertRaisesRegex(ContractValidationError, "provenance"):
                validate_article_tree(
                    tampered_tree,
                    document=document,
                    recipe=first,
                )

            tampered_recipe = json.loads(canonical_paper_recipe_json(first))
            tampered_recipe["actions"][0]["reason"] = "changed"
            with self.assertRaisesRegex(ContractValidationError, "action_id"):
                validate_paper_recipe(
                    tampered_recipe,
                    document=document,
                    evidence_root=evidence_root,
                )

    def test_layout_refinement_rejects_false_caption_and_keeps_figure(self):
        document = _document()
        with tempfile.TemporaryDirectory() as temp:
            evidence_root = Path(temp) / "source-evidence"
            write_pdfium_source_evidence(evidence_root, document)
            recipe = build_paper_recipe(document, evidence_root)
        layouts = (
            FinalLayout(
                source_sha256=document.source_sha256,
                page=LayoutPage.from_page(document.pages[0]),
                reviewer="fixture",
                prompt_version="fixture",
                regions=(
                    LayoutRegion(
                        "logo",
                        NormalizedBBox(0.10, 0.10, 0.20, 0.10),
                        "visual",
                        "figure",
                        1,
                        source_element_ids=("p0-logo",),
                    ),
                    LayoutRegion(
                        "citation",
                        NormalizedBBox(0.10, 0.20, 0.60, 0.10),
                        "text",
                        "caption",
                        2,
                        source_element_ids=("p0-sidebar",),
                    ),
                ),
            ),
            FinalLayout(
                source_sha256=document.source_sha256,
                page=LayoutPage.from_page(document.pages[1]),
                reviewer="fixture",
                prompt_version="fixture",
                regions=(
                    LayoutRegion(
                        "caption",
                        NormalizedBBox(0.10, 0.10, 0.60, 0.05),
                        "text",
                        "caption",
                        1,
                        source_element_ids=("p1-caption",),
                    ),
                    LayoutRegion(
                        "figure",
                        NormalizedBBox(0.10, 0.1375, 0.60, 0.325),
                        "visual",
                        "figure",
                        2,
                        source_element_ids=("p1-figure",),
                    ),
                    LayoutRegion(
                        "body",
                        NormalizedBBox(0.10, 0.50, 0.70, 0.10),
                        "text",
                        "body",
                        3,
                        source_element_ids=("p1-body",),
                    ),
                ),
            ),
        )
        refined = refine_layouts_with_recipe(document, layouts, recipe)
        first = {item.region_id: item for item in refined[0].regions}
        second = {item.region_id: item for item in refined[1].regions}
        self.assertEqual(first["logo"].content_class, "exclude")
        self.assertEqual(first["citation"].role, "margin")
        self.assertEqual(second["caption"].role, "caption")
        self.assertEqual(second["figure"].content_class, "visual")
        self.assertEqual(second["figure"].source_element_ids, ("p1-figure",))

    def test_raster_only_first_page_logo_is_bbox_scoped_furniture(self):
        document = _document()
        raster_region = SimpleNamespace(
            region_id="RV0002",
            bbox=NormalizedBBox(0.04, 0.54, 0.12, 0.05),
            page_area_ratio=0.006,
            residual_coverage=0.15,
        )
        analysis = SimpleNamespace(
            regions=(raster_region,),
            residual_mask_sha256="a" * 64,
        )
        with tempfile.TemporaryDirectory() as temp:
            evidence_root = Path(temp) / "source-evidence"
            write_pdfium_source_evidence(evidence_root, document)
            recipe = build_paper_recipe(
                document,
                evidence_root,
                raster_analyses={0: analysis},
            )
            validate_paper_recipe(
                recipe,
                document=document,
                evidence_root=evidence_root,
            )
        action = next(
            item
            for item in recipe["actions"]
            if item["reason"]
            == "small_uncaptioned_first_page_raster_furniture"
        )
        self.assertEqual(action["element_ids"], [])
        first_layout = FinalLayout(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(document.pages[0]),
            reviewer="fixture",
            prompt_version="fixture",
            regions=(
                LayoutRegion(
                    "raster-logo",
                    raster_region.bbox,
                    "visual",
                    "figure",
                    1,
                    source_element_ids=(),
                ),
            ),
        )
        second_layout = FinalLayout(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(document.pages[1]),
            reviewer="fixture",
            prompt_version="fixture",
            regions=(),
        )
        refined = refine_layouts_with_recipe(
            document,
            (first_layout, second_layout),
            recipe,
        )
        self.assertEqual(refined[0].regions[0].content_class, "exclude")

    def test_table_render_action_removes_native_numeric_text_from_body(self):
        document = _document()
        table_action = {
            "action_id": "recipe-table",
            "operation": "render",
            "page_index": 0,
            "element_ids": ["p0-sidebar"],
            "role": "table",
            "disposition": "render",
            "bbox": {"x": 0.1, "y": 0.5, "width": 0.7, "height": 0.1},
            "evidence_refs": ["claim-table"],
            "reason": "provider_table_boundary_preserved_as_image",
        }
        recipe = {"actions": [table_action]}
        layout = FinalLayout(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(document.pages[0]),
            reviewer="fixture",
            prompt_version="fixture",
            regions=(
                LayoutRegion(
                    "body",
                    NormalizedBBox(0.1, 0.2, 0.7, 0.1),
                    "text",
                    "body",
                    1,
                    source_element_ids=("p0-sidebar",),
                ),
            ),
        )
        second_layout = FinalLayout(
            source_sha256=document.source_sha256,
            page=LayoutPage.from_page(document.pages[1]),
            reviewer="fixture",
            prompt_version="fixture",
            regions=(),
        )
        refined = refine_layouts_with_recipe(
            document,
            (layout, second_layout),
            recipe,
        )
        body = next(item for item in refined[0].regions if item.region_id == "body")
        table = next(
            item for item in refined[0].regions if item.region_id != "body"
        )
        self.assertEqual(body.source_element_ids, ())
        self.assertEqual(table.content_class, "visual")
        self.assertEqual(table.role, "table")
        self.assertEqual(table.source_element_ids, ("p0-sidebar",))


if __name__ == "__main__":
    unittest.main()
