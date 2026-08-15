from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pdf_fixture_factory import create_born_digital_fixture


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _env():
    env = os.environ.copy()
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(SRC) + os.pathsep + str(ROOT / "tests") + (os.pathsep + current if current else "")
    )
    return env


def _run(argv, check=True):
    return subprocess.run(
        [sys.executable, *argv],
        cwd=ROOT,
        env=_env(),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=check,
    )


class RoutingPlanToolTests(unittest.TestCase):
    def test_l0_plan_executes_without_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "fixture.pdf"
            create_born_digital_fixture(pdf)
            roi_dir = root / "roi"
            review_dir = root / "review"
            output_dir = root / "out"

            _run(
                [
                    "-m", "paperwright", "layout-prepare",
                    str(pdf), str(roi_dir),
                    "--extraction-profile", "fast", "--preview-scale", "0.5",
                ]
            )
            roi_path = roi_dir / "content-roi.json"
            roi = json.loads(roi_path.read_text(encoding="utf-8"))
            roi["review_status"] = "confirmed"
            roi["reviewer"] = "test"
            roi_path.write_text(
                json.dumps(roi, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _run(
                [
                    "-m", "paperwright", "layout-prepare",
                    str(pdf), str(review_dir),
                    "--content-roi-json", str(roi_path),
                    "--extraction-profile", "fast", "--preview-scale", "0.5",
                ]
            )

            result = _run(
                [
                    str(ROOT / "tools" / "run_routing_plan.py"),
                    str(pdf), str(review_dir), str(output_dir),
                    "--extraction-profile", "fast", "--evidence", "minimal",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output_dir / "article.md").is_file())
            routing = json.loads(
                (review_dir / "routing.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                all(page["route"] == "L0_RULE" for page in routing["pages"])
            )
            for page_dir in sorted(review_dir.glob("page-*")):
                self.assertTrue((page_dir / "final-layout.json").is_file())

    def test_dry_run_prints_plan_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            review_dir.mkdir()
            (review_dir / "routing.json").write_text(
                json.dumps(
                    {
                        "contract_version": "paperwright-routing-v0.1",
                        "mode": "auto",
                        "source_sha256": "a" * 64,
                        "pages": [
                            {
                                "page_index": 0,
                                "route": "L0_RULE",
                                "reasons": [],
                                "signals": [],
                                "fallback_route": "L0_RULE",
                                "actions": [],
                            }
                        ],
                        "summary": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = _run(
                [
                    str(ROOT / "tools" / "run_routing_plan.py"),
                    "missing.pdf",
                    str(review_dir),
                    "out",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("visual-pages", result.stdout)
            self.assertIn("l0-pages 1", result.stdout)


if __name__ == "__main__":
    unittest.main()
