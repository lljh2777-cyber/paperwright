import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from paper2md.cli import main
from paper2md.exceptions import ContractValidationError
from paper2md.layout_dataset import export_layout_dataset
from paper2md.layout_models import FinalLayout, LayoutAction, LayoutRegion
from paper2md.layout_review import LAYOUT_REVIEW_PROMPT_VERSION

from tests.test_layout_stage_a import _final_layout, _task


def _write_review(root: Path, *, caption: bool = False) -> None:
    task = _task()
    layout = _final_layout(task)
    if caption:
        layout = FinalLayout(
            source_sha256=task.source_sha256,
            page=task.page,
            reviewer="fixture-ai",
            prompt_version=LAYOUT_REVIEW_PROMPT_VERSION,
            actions=(
                LayoutAction("A01", "keep", ("C01",), ("R01",)),
                LayoutAction("A02", "keep", ("C02",), ("R02",)),
                LayoutAction("A03", "keep", ("C03",), ("R03",)),
                LayoutAction(
                    "A04",
                    "attach-caption",
                    ("C02",),
                    ("R02",),
                    target_region_id="R03",
                ),
            ),
            regions=(
                LayoutRegion(
                    "R01",
                    task.candidates[0].bbox,
                    "text",
                    "body",
                    1,
                    ("C01",),
                ),
                LayoutRegion(
                    "R03",
                    task.candidates[2].bbox,
                    "visual",
                    "figure",
                    2,
                    ("C03",),
                ),
                LayoutRegion(
                    "R02",
                    task.candidates[1].bbox,
                    "text",
                    "caption",
                    3,
                    ("C02",),
                    parent_region_id="R03",
                ),
            ),
        )
    page = root / "page-0002"
    page.mkdir(parents=True)
    (page / "layout-task.json").write_text(
        task.canonical_json() + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (page / "final-layout.json").write_text(
        layout.canonical_json() + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class LayoutStageFTests(unittest.TestCase):
    def test_export_is_numeric_source_free_and_grouped_by_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "review"
            _write_review(review, caption=True)

            result = export_layout_dataset((review,), root / "dataset")
            manifest = result.manifest
            self.assertEqual(manifest["document_count"], 1)
            self.assertEqual(manifest["page_count"], 1)
            self.assertEqual(manifest["split_unit"], "document_id")
            self.assertFalse(manifest["contains_article_text"])
            self.assertFalse(manifest["contains_page_images"])
            self.assertFalse(manifest["contains_source_element_ids"])
            self.assertEqual(
                manifest["record_counts"],
                {
                    "candidate_labels": 3,
                    "action_labels": 3,
                    "reading_order_pairs": 3,
                    "caption_pairs": 1,
                    "content_roi_labels": 1,
                },
            )

            candidates = _jsonl(result.output_dir / "candidate_labels.jsonl")
            self.assertEqual(
                {(item["content_class"], item["role"]) for item in candidates},
                {("text", "body"), ("text", "caption"), ("visual", "figure")},
            )
            payload = (result.output_dir / "candidate_labels.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("p0001-text", payload)
            caption_pairs = _jsonl(result.output_dir / "caption_pairs.jsonl")
            self.assertTrue(caption_pairs[0]["is_attached"])
            content_rois = _jsonl(
                result.output_dir / "content_roi_labels.jsonl"
            )
            self.assertEqual(content_rois[0]["label_source"], "confirmed:fixture-ai")
            self.assertFalse(content_rois[0]["destructive_crop"])

    def test_export_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "review"
            _write_review(review, caption=True)
            first = export_layout_dataset((review,), root / "dataset-a")
            second = export_layout_dataset((review,), root / "dataset-b")
            first_files = {
                item.name: item.read_bytes()
                for item in first.output_dir.iterdir()
                if item.is_file()
            }
            second_files = {
                item.name: item.read_bytes()
                for item in second.output_dir.iterdir()
                if item.is_file()
            }
            self.assertEqual(first_files, second_files)

    def test_incomplete_review_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "review" / "page-0002"
            review.mkdir(parents=True)
            (review / "layout-task.json").write_text(
                _task().canonical_json(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ContractValidationError,
                "final-layout.json",
            ):
                export_layout_dataset((root / "review",), root / "dataset")

    def test_cli_exports_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "review"
            _write_review(review)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "layout-export-dataset",
                        str(root / "dataset"),
                        "--review-root",
                        str(review),
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "exported")
            self.assertEqual(payload["record_counts"]["candidate_labels"], 3)


if __name__ == "__main__":
    unittest.main()
