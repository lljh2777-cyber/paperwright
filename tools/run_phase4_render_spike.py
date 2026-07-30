#!/usr/bin/env python3
"""Run the bounded Phase 4 region-render spike on frozen local OA inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from paper2md.api import Paper2MD
from paper2md.backends import BackendRegistry
from paper2md.backends.pdfium import PDFiumBackend
from paper2md.config import Paper2MDConfig, RegionRenderPolicy


TARGETS = {
    "RW2-005": (2, 6),
    "RW2-007": (4,),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def registry() -> BackendRegistry:
    value = BackendRegistry()
    value.register("pdfium", PDFiumBackend())
    return value


def convert(source: Path, output: Path, pages: tuple[int, ...] = ()) -> None:
    config = Paper2MDConfig(
        region_render=RegionRenderPolicy(
            enabled=bool(pages),
            page_indices=pages,
        )
    )
    Paper2MD(config=config, registry=registry()).convert(source, output)


def tree_index(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads(
        (args.repo / "realworld/oa_sources.json").read_text(encoding="utf-8")
    )
    args.output_root.mkdir(parents=True, exist_ok=False)
    source_checks = []
    for paper in sources["papers"]:
        source = args.pdf_dir / f"{paper['id']}.pdf"
        actual = {
            "id": paper["id"],
            "size_bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
        if (
            actual["size_bytes"] != paper["size_bytes"]
            or actual["sha256"] != paper["sha256"]
        ):
            raise RuntimeError(f"{paper['id']} source identity mismatch")
        source_checks.append(actual)
        convert(
            source,
            args.output_root / "regression" / paper["id"],
        )

    for run in ("run1", "run2"):
        for paper_id, page_indices in TARGETS.items():
            convert(
                args.pdf_dir / f"{paper_id}.pdf",
                args.output_root / "targets" / run / paper_id,
                page_indices,
            )

    determinism = []
    for paper_id in sorted(TARGETS):
        first = tree_index(args.output_root / "targets/run1" / paper_id)
        second = tree_index(args.output_root / "targets/run2" / paper_id)
        determinism.append(
            {
                "paper_id": paper_id,
                "run1_file_count": len(first),
                "run2_file_count": len(second),
                "identical": first == second,
                "run1": first,
                "run2": second,
            }
        )
    index = {
        "schema_version": "paper2md-phase4-render-spike-runtime-index-v1",
        "source_checks": source_checks,
        "regression_documents": len(sources["papers"]),
        "target_document_runs": 4,
        "target_page_observations": 6,
        "determinism": determinism,
    }
    (args.output_root / "runtime_index.json").write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
