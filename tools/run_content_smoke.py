#!/usr/bin/env python3
"""Content-level smoke test using only a temporary self-generated PDF."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from PIL import Image

from paperwright.api import PaperWright
from paperwright.backends.pdfium import PDFiumBackend
from paperwright.config import PaperWrightConfig
from paperwright.manifest import sha256_file, validate_manifest
from paperwright.models import PhysicalDocument

from pdf_fixture_factory import create_born_digital_fixture


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    checks: list[dict[str, object]] = []

    def record(check_id: str, actual: object, expected: object) -> None:
        checks.append(
            {
                "check_id": check_id,
                "actual": actual,
                "expected": expected,
                "pass": actual == expected,
            }
        )

    with tempfile.TemporaryDirectory(prefix="paperwright-stage-b-smoke-") as temporary:
        root = Path(temporary)
        source = root / "fixture.pdf"
        fixture = create_born_digital_fixture(source)
        product = PaperWright(PaperWrightConfig(workspace_root=root))
        product.register_backend("pdfium", PDFiumBackend())
        first = product.convert(source, root / "run1")
        second = product.convert(source, root / "run2")
        article = (first.output_dir / "article.md").read_text(encoding="utf-8")
        manifest = json.loads(
            (first.output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        validate_manifest(manifest)
        physical = PhysicalDocument.from_dict(
            json.loads(
                (first.output_dir / "physical_document.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        image_record = manifest["images"][0]
        image_path = first.output_dir / image_record["path"]
        with Image.open(image_path) as image:
            image_observation = {
                "format": image.format,
                "size": list(image.size),
                "color_count": len(image.convert("RGB").getcolors(maxcolors=1000) or []),
            }

        record("fixture_pages", fixture["pages"], 2)
        record("manifest_source_hash", manifest["source_sha256"], fixture["sha256"])
        record("physical_pages", len(physical.pages), 2)
        record("title", article.startswith("# PaperWright Fixture Title\n"), True)
        record(
            "double_column_order",
            [
                article.index("LEFT-ONE"),
                article.index("LEFT-TWO"),
                article.index("RIGHT-ONE"),
                article.index("RIGHT-TWO"),
            ],
            sorted(
                [
                    article.index("LEFT-ONE"),
                    article.index("LEFT-TWO"),
                    article.index("RIGHT-ONE"),
                    article.index("RIGHT-TWO"),
                ]
            ),
        )
        record("unicode_cafe", "Café" in article, True)
        record("table_degraded_count", len(manifest["degraded"]), 1)
        record("fabricated_table_grid", "| Group | Value |" in article, False)
        record("image_count", len(manifest["images"]), 1)
        record(
            "image_hash",
            sha256_file(image_path),
            image_record["sha256"],
        )
        record(
            "image_content",
            image_observation,
            {"format": "PNG", "size": [16, 12], "color_count": 24},
        )
        record(
            "manifest_element_coverage",
            len(manifest["elements"]),
            sum(len(page.elements) for page in physical.pages),
        )
        record(
            "two_run_tree_determinism",
            tree_hashes(first.output_dir),
            tree_hashes(second.output_dir),
        )
        summary = {
            "schema_version": "paperwright-v2-mvp-smoke-v1",
            "fixture": fixture,
            "backend": manifest["backend"],
            "check_count": len(checks),
            "pass_count": sum(bool(item["pass"]) for item in checks),
            "failure_count": sum(not bool(item["pass"]) for item in checks),
            "checks": checks,
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
