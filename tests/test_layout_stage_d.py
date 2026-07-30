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
)
from paper2md.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paper2md.manifest import (
    HYBRID_LAYOUT_MANIFEST_VERSION,
    sha256_file,
    validate_manifest,
)

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
    def _prepare(self, root: Path) -> tuple[Path, Path]:
        source = root / "fixture.pdf"
        review = root / "review"
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
        return source, review

    def test_layout_apply_writes_markdown_visuals_and_v06_manifest(self):
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
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            validate_manifest(manifest)
            self.assertFalse(manifest["layout_review"]["ocr_used"])
            provenance = output / manifest["layout_review"]["provenance_path"]
            self.assertEqual(
                sha256_file(provenance),
                manifest["layout_review"]["provenance_sha256"],
            )
            self.assertIn(
                "layout-region:",
                (output / "article.md").read_text(encoding="utf-8"),
            )

    def test_layout_apply_is_byte_deterministic(self):
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
                }

            self.assertEqual(content(outputs[0]), content(outputs[1]))

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
