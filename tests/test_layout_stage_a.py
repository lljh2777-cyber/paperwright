import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from paper2md.cli import main
from paper2md.exceptions import ContractValidationError, OutputConflictError
from paper2md.layout_export import (
    export_layout_task_bundle,
    render_layout_overlay,
)
from paper2md.layout_models import (
    FinalLayout,
    LayoutAction,
    LayoutCandidate,
    LayoutPage,
    LayoutRegion,
    LayoutSeparator,
    LayoutTask,
    NormalizedBBox,
)
from paper2md.layout_review import LAYOUT_REVIEW_PROMPT_VERSION
from paper2md.models import BBox


def _task() -> LayoutTask:
    page = LayoutPage(page_index=1, width=600, height=800, rotation=0)
    candidates = (
        LayoutCandidate(
            candidate_id="C01",
            bbox=NormalizedBBox(0.05, 0.05, 0.42, 0.25),
            source_element_ids=("p0001-text-0001",),
            element_kinds=("text",),
            features={
                "native_text_available": True,
                "native_text_coverage": 0.61,
            },
        ),
        LayoutCandidate(
            candidate_id="C02",
            bbox=NormalizedBBox(0.53, 0.05, 0.42, 0.25),
            source_element_ids=("p0001-text-0002",),
            element_kinds=("text",),
            features={
                "native_text_available": True,
                "native_text_coverage": 0.64,
            },
        ),
        LayoutCandidate(
            candidate_id="C03",
            bbox=NormalizedBBox(0.05, 0.35, 0.90, 0.45),
            source_element_ids=("p0001-image-0001", "p0001-vector-0001"),
            element_kinds=("image", "vector"),
            features={
                "native_text_available": True,
                "native_text_coverage": 0.08,
                "image_coverage": 0.31,
                "drawing_coverage": 0.42,
            },
        ),
    )
    separator = LayoutSeparator(
        separator_id="S01",
        orientation="vertical",
        bbox=NormalizedBBox(0.48, 0.05, 0.04, 0.25),
        adjacent_candidate_ids=("C01", "C02"),
        features={"occupancy_ratio": 0.0},
    )
    return LayoutTask(
        source_sha256="a" * 64,
        page=page,
        candidate_generator_version="whitespace-v0.1",
        feature_schema_version="layout-features-v0.1",
        candidates=candidates,
        separators=(separator,),
        metadata={
            "analysis_roi": {
                "bbox": {
                    "x": 0.04,
                    "y": 0.04,
                    "width": 0.92,
                    "height": 0.78,
                },
                "source": "confirmed:fixture-ai",
                "coordinate_system": (
                    "top-left/original-page-normalized/y-down"
                ),
                "destructive_crop": False,
            },
            "excluded_element_ids": [],
            "boundary_crossing_element_ids": [],
        },
    )


def _final_layout(task: LayoutTask) -> FinalLayout:
    return FinalLayout(
        source_sha256=task.source_sha256,
        page=task.page,
        reviewer="fixture-ai",
        prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
        actions=(
            LayoutAction(
                action_id="A01",
                action="keep",
                source_candidate_ids=("C01",),
                result_region_ids=("R01",),
            ),
            LayoutAction(
                action_id="A02",
                action="keep",
                source_candidate_ids=("C02",),
                result_region_ids=("R02",),
            ),
            LayoutAction(
                action_id="A03",
                action="keep",
                source_candidate_ids=("C03",),
                result_region_ids=("R03",),
            ),
        ),
        regions=(
            LayoutRegion(
                region_id="R01",
                bbox=task.candidates[0].bbox,
                content_class="text",
                role="body",
                order=1,
                source_candidate_ids=("C01",),
            ),
            LayoutRegion(
                region_id="R02",
                bbox=task.candidates[1].bbox,
                content_class="text",
                role="body",
                order=2,
                source_candidate_ids=("C02",),
            ),
            LayoutRegion(
                region_id="R03",
                bbox=task.candidates[2].bbox,
                content_class="visual",
                role="figure",
                order=3,
                source_candidate_ids=("C03",),
            ),
        ),
    )


class LayoutStageATests(unittest.TestCase):
    def test_normalized_bbox_round_trip(self):
        original = BBox(30, 80, 240, 320)
        normalized = NormalizedBBox.from_pdf_bbox(
            original,
            page_width=600,
            page_height=800,
        )
        self.assertEqual(
            normalized.to_pdf_bbox(page_width=600, page_height=800),
            original,
        )
        self.assertEqual(
            normalized.to_pixel_box(image_width=1200, image_height=1600),
            (60, 160, 539, 799),
        )

    def test_task_round_trip_is_deterministic(self):
        task = _task()
        restored = LayoutTask.from_dict(json.loads(task.canonical_json()))
        self.assertEqual(restored, task)
        self.assertEqual(
            restored.deterministic_sha256(),
            task.deterministic_sha256(),
        )

    def test_task_rejects_unknown_separator_candidate(self):
        with self.assertRaisesRegex(
            ContractValidationError,
            "未知候选区块",
        ):
            LayoutTask(
                source_sha256="b" * 64,
                page=LayoutPage(0, 100, 100, 0),
                candidate_generator_version="fixture",
                feature_schema_version="fixture",
                candidates=(),
                separators=(
                    LayoutSeparator(
                        "S01",
                        "vertical",
                        NormalizedBBox(0.4, 0.1, 0.1, 0.8),
                        ("C01", "C02"),
                    ),
                ),
            )

    def test_final_layout_validates_against_task(self):
        task = _task()
        layout = _final_layout(task)
        layout.validate_against(task)
        restored = FinalLayout.from_dict(json.loads(layout.canonical_json()))
        self.assertEqual(restored, layout)

    def test_final_layout_rejects_duplicate_reading_order(self):
        task = _task()
        with self.assertRaisesRegex(
            ContractValidationError,
            "阅读顺序不能重复",
        ):
            FinalLayout(
                source_sha256=task.source_sha256,
                page=task.page,
                reviewer="fixture-ai",
                prompt_version="v1",
                regions=(
                    LayoutRegion(
                        "R01",
                        task.candidates[0].bbox,
                        "text",
                        "body",
                        1,
                    ),
                    LayoutRegion(
                        "R02",
                        task.candidates[1].bbox,
                        "text",
                        "body",
                        1,
                    ),
                ),
            )

    def test_overlay_is_deterministic_and_does_not_mutate_preview(self):
        task = _task()
        preview = Image.new("RGB", (600, 800), "white")
        before = preview.tobytes()
        first = render_layout_overlay(preview, task)
        second = render_layout_overlay(preview, task)
        self.assertEqual(preview.tobytes(), before)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertNotEqual(first.tobytes(), before)

    def test_bundle_export_refuses_overwrite(self):
        task = _task()
        preview = Image.new("RGB", (300, 400), "white")
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "layout-task"
            export_layout_task_bundle(destination, task, preview)
            self.assertEqual(
                LayoutTask.from_dict(
                    json.loads(
                        (destination / "layout-task.json").read_text(
                            encoding="utf-8"
                        )
                    )
                ),
                task,
            )
            self.assertTrue((destination / "page.png").is_file())
            self.assertTrue((destination / "content-roi.png").is_file())
            self.assertTrue((destination / "overlay.png").is_file())
            self.assertTrue(
                (destination / "review-instructions.md").is_file()
            )
            with self.assertRaises(OutputConflictError):
                export_layout_task_bundle(destination, task, preview)

    def test_cli_validates_task_and_final_layout(self):
        task = _task()
        layout = _final_layout(task)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_path = root / "task.json"
            layout_path = root / "layout.json"
            task_path.write_text(task.canonical_json(), encoding="utf-8")
            layout_path.write_text(layout.canonical_json(), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["validate-layout-task", str(task_path)])
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["candidate_count"], 3)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "validate-final-layout",
                        str(layout_path),
                        "--task",
                        str(task_path),
                    ]
                )
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["validated_against_task"])


if __name__ == "__main__":
    unittest.main()
