import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from paper2md.cli import main
from paper2md.exceptions import ContractValidationError
from paper2md.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutRegion,
)
from paper2md.layout_review import (
    LAYOUT_REVIEW_PROMPT_VERSION,
    build_layout_review_instructions,
    validate_layout_review,
)

from tests.test_layout_stage_a import _final_layout, _task
from pdf_fixture_factory import create_born_digital_fixture


class LayoutStageCTests(unittest.TestCase):
    def test_valid_review_accounts_for_all_candidates(self):
        task = _task()
        validate_layout_review(_final_layout(task), task)

    def test_prompt_forbids_transcription_and_identifies_task(self):
        task = _task()
        prompt = build_layout_review_instructions(task)
        self.assertIn(task.deterministic_sha256(), prompt)
        self.assertIn("Never transcribe, rewrite, summarize", prompt)
        self.assertIn(LAYOUT_REVIEW_PROMPT_VERSION, prompt)

    def test_review_rejects_unassigned_candidate(self):
        task = _task()
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-ai",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=(
                LayoutRegion(
                    "R01",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    1,
                    source_candidate_ids=("C01",),
                ),
            ),
            actions=(
                LayoutAction(
                    "A01",
                    "keep",
                    source_candidate_ids=("C01",),
                    result_region_ids=("R01",),
                ),
            ),
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "未被最终区块引用或 discard",
        ):
            validate_layout_review(layout, task)

    def test_review_rejects_invented_element_provenance(self):
        task = _task()
        layout = _final_layout(task)
        tampered = FinalLayout(
            source_sha256=layout.source_sha256,
            page=layout.page,
            reviewer=layout.reviewer,
            prompt_version=layout.prompt_version,
            actions=layout.actions,
            regions=(
                LayoutRegion(
                    "R01",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    1,
                    source_candidate_ids=("C01",),
                    source_element_ids=("invented-element",),
                ),
                *layout.regions[1:],
            ),
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "source_element_ids",
        ):
            validate_layout_review(tampered, task)

    def test_review_rejects_multiple_assignment_without_split(self):
        task = _task()
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-ai",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=(
                LayoutRegion(
                    "R01",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    1,
                    source_candidate_ids=("C01",),
                ),
                LayoutRegion(
                    "R02",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    2,
                    source_candidate_ids=("C01",),
                ),
            ),
            actions=(
                LayoutAction(
                    "A01",
                    "keep",
                    source_candidate_ids=("C01",),
                    result_region_ids=("R01",),
                ),
                LayoutAction(
                    "A02",
                    "discard",
                    source_candidate_ids=("C02",),
                ),
                LayoutAction(
                    "A03",
                    "discard",
                    source_candidate_ids=("C03",),
                ),
            ),
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "没有 split 动作",
        ):
            validate_layout_review(layout, task)

    def test_caption_parent_must_be_visual(self):
        task = _task()
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-ai",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            regions=(
                LayoutRegion(
                    "R01",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    1,
                    source_candidate_ids=("C01",),
                ),
                LayoutRegion(
                    "R02",
                    task.candidates[1].bbox,
                    "text",
                    "caption",
                    2,
                    source_candidate_ids=("C02",),
                    parent_region_id="R01",
                ),
                LayoutRegion(
                    "R03",
                    task.candidates[2].bbox,
                    "visual",
                    "figure",
                    3,
                    source_candidate_ids=("C03",),
                ),
            ),
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "父区块必须是 visual",
        ):
            validate_layout_review(layout, task)

    def test_layout_prepare_exports_deterministic_review_bundles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            create_born_digital_fixture(source)
            outputs = []
            for name in ("review-a", "review-b"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(
                        [
                            "layout-prepare",
                            str(source),
                            str(root / name),
                            "--workspace-root",
                            str(root),
                        ]
                    )
                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["status"], "prepared")
                self.assertFalse(payload["ocr_used"])
                outputs.append(root / name)

            index = json.loads(
                (outputs[0] / "review-index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(index["page_count"], 2)
            self.assertEqual(index["review_mode"], "visual-direct")
            for page in index["pages"]:
                page_root = outputs[0] / page["directory"]
                self.assertEqual(
                    {item.name for item in page_root.iterdir()},
                    {
                        "layout-task.json",
                        "page.png",
                        "content-roi.png",
                        "overlay.png",
                        "review-instructions.md",
                    },
                )
                with Image.open(page_root / "overlay.png") as image:
                    self.assertEqual(image.format, "PNG")
                    with Image.open(page_root / "page.png") as preview:
                        self.assertEqual(
                            image.convert("RGB").tobytes(),
                            preview.convert("RGB").tobytes(),
                        )
                task = json.loads(
                    (page_root / "layout-task.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    task["metadata"]["review_mode"],
                    "visual-direct",
                )
                self.assertEqual(task["candidates"], [])
                self.assertEqual(task["separators"], [])

            def content(root_path: Path):
                return {
                    path.relative_to(root_path).as_posix(): path.read_bytes()
                    for path in root_path.rglob("*")
                    if path.is_file()
                }

            self.assertEqual(content(outputs[0]), content(outputs[1]))

    def test_fast_layout_prepare_exports_raster_v2_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.pdf"
            destination = root / "review-fast"
            create_born_digital_fixture(source)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "layout-prepare",
                        str(source),
                        str(destination),
                        "--workspace-root",
                        str(root),
                        "--extraction-profile",
                        "fast",
                        "--review-mode",
                        "candidate-assisted",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["extraction_profile"], "fast")
            index = json.loads(
                (destination / "review-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["extraction_profile"], "fast")
            self.assertEqual(index["review_mode"], "candidate-assisted")
            self.assertEqual(
                index["physical_extraction_profile"],
                "text-only-fast",
            )
            self.assertEqual(
                index["layout_task_versions"],
                ["paper2md-layout-task-v0.2"],
            )
            cache = index["extraction_cache"]
            self.assertEqual(
                cache["schema_version"],
                "paper2md-layout-extraction-cache-v0.1",
            )
            self.assertTrue(
                (
                    destination
                    / cache["physical_document"]["path"]
                ).is_file()
            )
            self.assertEqual(
                len(cache["physical_document"]["sha256"]),
                64,
            )
            task = json.loads(
                (
                    destination
                    / index["pages"][0]["directory"]
                    / "layout-task.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                task["contract_version"],
                "paper2md-layout-task-v0.2",
            )
            self.assertTrue(
                any("raster" in item["element_kinds"] for item in task["candidates"])
            )

    def test_layout_prepare_corrupt_input_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "corrupt.pdf"
            source.write_bytes(b"%PDF-1.4\ncorrupt")
            destination = root / "review"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "layout-prepare",
                        str(source),
                        str(destination),
                        "--workspace-root",
                        str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
