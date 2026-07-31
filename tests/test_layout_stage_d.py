import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from paper2md.cli import main
from paper2md.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutRegion,
    LayoutTask,
    NormalizedBBox,
)
from paper2md.layout_writer import _text_region_non_text_diagnostics
from paper2md.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paper2md.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    sha256_file,
    validate_manifest,
)
from paper2md.models import BBox, Element, Page, Provenance

from pdf_fixture_factory import create_born_digital_fixture


def _write_fixture_reviews(review_root: Path) -> None:
    for page_root in sorted(review_root.glob("page-*")):
        task = LayoutTask.from_dict(
            json.loads(
                (page_root / "layout-task.json").read_text(encoding="utf-8")
            )
        )
        regions = []
        actions = []
        order = 1
        for index, candidate in enumerate(task.candidates, start=1):
            region_id = f"R{index:03d}"
            peripheral = bool(candidate.features.get("peripheral_hint"))
            visual = bool(
                {"image", "vector"} & set(candidate.element_kinds)
            )
            content_class = (
                "exclude" if peripheral else "visual" if visual else "text"
            )
            role = (
                "footer"
                if peripheral and candidate.bbox.y > 0.5
                else "header"
                if peripheral
                else "figure"
                if visual
                else "body"
            )
            regions.append(
                LayoutRegion(
                    region_id=region_id,
                    bbox=candidate.bbox,
                    content_class=content_class,
                    role=role,
                    order=None if peripheral else order,
                    source_candidate_ids=(candidate.candidate_id,),
                )
            )
            actions.append(
                LayoutAction(
                    action_id=f"A{index:03d}",
                    action="keep",
                    source_candidate_ids=(candidate.candidate_id,),
                    result_region_ids=(region_id,),
                )
            )
            if not peripheral:
                order += 1
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-layout-reviewer",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=tuple(regions),
            actions=tuple(actions),
        )
        (page_root / "final-layout.json").write_text(
            layout.canonical_json(),
            encoding="utf-8",
        )


class LayoutStageDTests(unittest.TestCase):
    def test_text_regions_ignore_rules_and_heading_backgrounds_only(self):
        provenance = Provenance("fixture", "native", "fixture")
        elements = (
            Element("text", "text", 0, BBox(10, 10, 40, 8), provenance),
            Element("rule", "vector", 0, BBox(10, 20, 50, 0.2), provenance),
            Element("background", "vector", 0, BBox(10, 10, 50, 10), provenance),
            Element("image", "image", 0, BBox(60, 10, 20, 20), provenance),
        )
        page = Page(0, 100, 100, 0, elements)
        region = LayoutRegion(
            region_id="R001",
            bbox=NormalizedBBox(0.1, 0.1, 0.5, 0.1),
            content_class="text",
            role="heading",
            order=1,
            source_element_ids=("text", "rule", "background", "image"),
        )

        result = _text_region_non_text_diagnostics(page, region)

        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["ignored_decorative_count"], 2)
        self.assertEqual(result["risk_count"], 1)
        self.assertEqual(result["by_class"]["decorative_rule"], 1)
        self.assertEqual(result["by_class"]["heading_background"], 1)
        self.assertEqual(result["by_class"]["semantic_non_text"], 1)

    def _prepare(
        self,
        root: Path,
        *,
        include_references: bool = False,
    ) -> tuple[Path, Path]:
        source = root / "fixture.pdf"
        proposal = root / "roi-proposal"
        review = root / "review"
        create_born_digital_fixture(
            source,
            include_references=include_references,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "layout-prepare",
                    str(source),
                    str(proposal),
                    "--workspace-root",
                    str(root),
                ]
            )
        self.assertEqual(code, 0)
        roi_path = proposal / "content-roi.json"
        roi = json.loads(roi_path.read_text(encoding="utf-8"))
        roi["review_status"] = "confirmed"
        roi["reviewer"] = "fixture-roi-reviewer"
        roi_path.write_text(
            json.dumps(
                roi,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "layout-prepare",
                    str(source),
                    str(review),
                    "--content-roi-json",
                    str(roi_path),
                    "--workspace-root",
                    str(root),
                ]
            )
        self.assertEqual(code, 0)
        _write_fixture_reviews(review)
        return source, review

    def test_layout_apply_rejects_unconfirmed_content_roi(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            review = root / "review"
            output = root / "output"
            create_born_digital_fixture(source)
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-prepare",
                        str(source),
                        str(review),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            _write_fixture_reviews(review)
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())

    def test_reference_section_can_be_omitted_or_written_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(
                root,
                include_references=True,
            )
            omitted = root / "omitted"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(omitted),
                        "--references",
                        "omit",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            omitted_article = (omitted / "article.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Smith AB", omitted_article)
            self.assertNotIn("Acknowledgments", omitted_article)
            self.assertNotIn("fixture reviewers", omitted_article)
            self.assertNotIn("Author Contributions", omitted_article)
            self.assertIn("Supplementary Information", omitted_article)
            self.assertIn("Supplementary Figure S1", omitted_article)
            self.assertFalse((omitted / "references.md").exists())

            separated = root / "separated"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(separated),
                        "--references",
                        "separate",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            separated_article = (separated / "article.md").read_text(
                encoding="utf-8"
            )
            references = (separated / "references.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Smith AB", separated_article)
            self.assertNotIn("Acknowledgments", separated_article)
            self.assertNotIn("fixture reviewers", separated_article)
            self.assertNotIn("Author Contributions", separated_article)
            self.assertIn("Supplementary Information", separated_article)
            self.assertIn("Supplementary Figure S1", separated_article)
            self.assertIn("# References", references)
            self.assertIn("Smith AB", references)
            self.assertNotIn("Acknowledgments", references)
            provenance = json.loads(
                (
                    separated
                    / "_paper2md"
                    / "04-provenance"
                    / "layout-provenance.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["references"]["status"], "detected")
            self.assertEqual(
                provenance["references"]["output_path"],
                "references.md",
            )
            self.assertEqual(
                provenance["references"]["detection_method"],
                "heading_and_entries",
            )
            self.assertIsNotNone(
                provenance["references"]["end_page_index"]
            )
            self.assertGreater(
                provenance["references"][
                    "omitted_back_matter_paragraphs"
                ],
                0,
            )

    def test_layout_apply_writes_standard_self_contained_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            output = root / "output"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(
                payload["manifest_version"],
                HYBRID_LAYOUT_MANIFEST_VERSION,
            )
            self.assertTrue((output / "article.md").is_file())
            self.assertTrue(any((output / "images").glob("*.png")))
            self.assertTrue((output / "images" / "figure-0001.png").is_file())
            manifest = json.loads(
                (
                    output / "_paper2md" / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            self.assertFalse(manifest["layout_review"]["ocr_used"])
            self.assertEqual(
                manifest["layout_review"]["evidence_level"],
                "standard",
            )
            provenance = output / manifest["layout_review"]["provenance_path"]
            self.assertEqual(
                sha256_file(provenance),
                manifest["layout_review"]["provenance_sha256"],
            )
            self.assertIn(
                "layout-region:",
                (output / "article.md").read_text(encoding="utf-8"),
            )
            article = (output / "article.md").read_text(encoding="utf-8")
            comments = [
                line
                for line in article.splitlines()
                if line.startswith("<!-- layout-region:")
            ]
            self.assertTrue(comments)
            self.assertTrue(all(len(line) < 260 for line in comments))
            self.assertTrue(all("element-count:" in line for line in comments))
            self.assertTrue(all("elements-sha256:" in line for line in comments))
            self.assertNotIn("; elements: ", article)
            provenance_value = json.loads(provenance.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    region["source_element_ids"]
                    for page in provenance_value["pages"]
                    for region in page["regions"]
                )
            )
            self.assertFalse(
                (
                    output
                    / "_paper2md"
                    / "01-physical"
                    / "physical-document.json"
                ).exists()
            )
            self.assertTrue(
                (output / "_paper2md" / "02-roi" / "content-roi.json").is_file()
            )
            self.assertTrue(
                (
                    output
                    / "_paper2md"
                    / "03-layout"
                    / "page-0001-overlay.png"
                ).is_file()
            )
            validation = json.loads(
                (
                    output
                    / "_paper2md"
                    / "05-validation"
                    / "validation-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(validation["checks"]["ocr_not_used"])
            self.assertEqual(
                set(validation["quality_checks"]),
                {
                    "markdown_text",
                    "figure_label_leakage",
                    "title_integrity",
                    "image_links",
                    "layout_element_coverage",
                    "layout_element_uniqueness",
                    "manifest_inventory",
                    "native_object_diagnostics",
                    "text_reconstruction",
                },
            )
            self.assertEqual(
                validation["quality_checks"]["image_links"]["status"],
                "pass",
            )
            self.assertEqual(
                validation["quality_checks"]["text_reconstruction"]["status"],
                "pass",
            )
            self.assertIn("warning_summary", validation)
            self.assertIn(
                "actionable_findings",
                validation["warning_summary"],
            )
            self.assertTrue(
                validation["checks"]["manifest_inventory_complete"]
            )
            self.assertTrue(
                (
                    output
                    / "_paper2md"
                    / "03-layout"
                    / "page-0001-final-layout.json"
                ).is_file()
            )
            self.assertFalse(
                any(
                    (output / "_paper2md" / "03-layout").glob(
                        "*-layout-task.json"
                    )
                )
            )
            self.assertTrue((output / "_paper2md" / "run.json").is_file())
            self.assertTrue((output / "_paper2md" / "source.json").is_file())
            self.assertTrue(
                (
                    output
                    / "_paper2md"
                    / "05-validation"
                    / "validation-report.md"
                ).is_file()
            )

    def test_layout_apply_supports_minimal_and_full_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            minimal = root / "minimal"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(minimal),
                        "--evidence",
                        "minimal",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                {item.name for item in minimal.iterdir()},
                {"article.md", "images", "_paper2md"},
            )
            self.assertEqual(
                {
                    item.relative_to(minimal).as_posix()
                    for item in (minimal / "_paper2md").rglob("*")
                    if item.is_file()
                },
                {"_paper2md/manifest.json"},
            )
            minimal_manifest = json.loads(
                (minimal / "_paper2md" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                minimal_manifest["layout_review"]["evidence_level"],
                "minimal",
            )
            self.assertIsNone(
                minimal_manifest["layout_review"]["provenance_path"]
            )

            full = root / "full"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(full),
                        "--evidence",
                        "full",
                        "--include-source-pdf",
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            evidence = full / "_paper2md"
            self.assertTrue(
                (evidence / "01-physical" / "physical-document.json").is_file()
            )
            self.assertTrue(
                (evidence / "02-roi" / "page-0001-content-roi.png").is_file()
            )
            self.assertTrue(
                (evidence / "03-layout" / "page-0001-layout-task.json").is_file()
            )
            self.assertTrue(
                (evidence / "03-layout" / "page-0001-page.png").is_file()
            )
            self.assertEqual(
                (evidence / "source.pdf").read_bytes(), source.read_bytes()
            )

    def test_layout_apply_core_artifacts_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            outputs = []
            for name in ("output-a", "output-b"):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            "layout-apply",
                            str(source),
                            str(review),
                            str(root / name),
                            "--workspace-root",
                            str(root),
                        ]
                    )
                self.assertEqual(code, 0)
                outputs.append(root / name)

            def content(output_root: Path):
                return {
                    path.relative_to(output_root).as_posix(): path.read_bytes()
                    for path in output_root.rglob("*")
                    if path.is_file()
                    and path.name not in {"run.json", "manifest.json"}
                }

            self.assertEqual(content(outputs[0]), content(outputs[1]))
            for output in outputs:
                run = json.loads(
                    (output / "_paper2md" / "run.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(run["completed_at_utc"].endswith("+00:00"))

    def test_layout_apply_rejects_stale_task_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, review = self._prepare(root)
            task_path = review / "page-0001" / "layout-task.json"
            value = json.loads(task_path.read_text(encoding="utf-8"))
            value["candidate_generator_version"] = "stale-generator"
            task_path.write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
            output = root / "output"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "layout-apply",
                        str(source),
                        str(review),
                        str(output),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
