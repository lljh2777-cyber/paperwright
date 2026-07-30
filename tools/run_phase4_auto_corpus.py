#!/usr/bin/env python3
"""Run default/auto modes on the frozen eight-paper local OA corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import time
from pathlib import Path

from paper2md.api import Paper2MD
from paper2md.backends import BackendRegistry
from paper2md.backends.pdfium import PDFiumBackend
from paper2md.config import Paper2MDConfig, RegionRenderPolicy


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def app(mode: str, max_candidates: int) -> Paper2MD:
    registry = BackendRegistry()
    registry.register("pdfium", PDFiumBackend())
    return Paper2MD(
        config=Paper2MDConfig(
            region_render=RegionRenderPolicy(
                mode=mode,
                max_candidates_per_document=max_candidates,
            )
        ),
        registry=registry,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("off", "auto"), required=True)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--max-candidates", type=int, default=12)
    args = parser.parse_args()
    if args.mode == "off" and args.baseline_root is None:
        parser.error("--baseline-root is required for off mode")
    if args.output_root.exists():
        raise RuntimeError("output root already exists")

    sources = json.loads(
        (args.repo / "realworld/oa_sources.json").read_text(encoding="utf-8")
    )
    args.output_root.mkdir(parents=True)
    backend = PDFiumBackend()
    source_checks = []
    first_runs = []
    converter = app(args.mode, args.max_candidates)
    for paper in sources["papers"]:
        paper_id = paper["id"]
        source = args.pdf_dir / f"{paper_id}.pdf"
        check = {
            "paper_id": paper_id,
            "size_bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
        check["match"] = (
            check["size_bytes"] == paper["size_bytes"]
            and check["sha256"] == paper["sha256"]
        )
        if not check["match"]:
            raise RuntimeError(f"{paper_id} source identity mismatch")
        source_checks.append(check)
        output = args.output_root / "first" / paper_id
        started = time.monotonic()
        result = converter.convert(source, output)
        wall = time.monotonic() - started
        output_tree = tree(output)
        rendered = sum(
            item["extraction_mode"] == "region-rendered"
            for item in result.manifest["figures"]
        )
        first_runs.append(
            {
                "paper_id": paper_id,
                "wall_seconds": wall,
                "page_count": result.manifest["page_count"],
                "rendered_count": rendered,
                "file_count": len(output_tree),
                "total_bytes": sum(
                    int(item["size_bytes"]) for item in output_tree
                ),
                "tree": output_tree,
            }
        )

    repeats = []
    if args.mode == "auto":
        selected = [
            item["paper_id"]
            for item in first_runs
            if item["rendered_count"] > 0
        ]
        for paper in sources["papers"]:
            if len(selected) >= 4:
                break
            if paper["id"] not in selected:
                selected.append(paper["id"])
        for paper_id in selected:
            output = args.output_root / "repeat" / paper_id
            started = time.monotonic()
            converter.convert(
                args.pdf_dir / f"{paper_id}.pdf",
                output,
            )
            wall = time.monotonic() - started
            first_tree = next(
                item["tree"]
                for item in first_runs
                if item["paper_id"] == paper_id
            )
            repeat_tree = tree(output)
            repeats.append(
                {
                    "paper_id": paper_id,
                    "wall_seconds": wall,
                    "file_count": len(repeat_tree),
                    "total_bytes": sum(
                        int(item["size_bytes"]) for item in repeat_tree
                    ),
                    "identical_to_first": repeat_tree == first_tree,
                    "tree": repeat_tree,
                }
            )
    else:
        for item in first_runs:
            baseline = tree(args.baseline_root / item["paper_id"])
            item["identical_to_authoritative_baseline"] = (
                item["tree"] == baseline
            )

    index = {
        "schema_version": "paper2md-phase4-auto-runtime-index-v1",
        "mode": args.mode,
        "base_commit": "25e4ecea02979cf7dcb56ab2d280425bc56e74e2",
        "runtime": {
            "python_package_pypdfium2": importlib.metadata.version(
                "pypdfium2"
            ),
            "pdfium": backend.identity.engine_version,
            "libpdfium_sha256": backend.identity.binary_sha256,
            "pillow": importlib.metadata.version("Pillow"),
        },
        "max_candidates_per_document": args.max_candidates,
        "source_checks": source_checks,
        "first_runs": first_runs,
        "repeat_runs": repeats,
        "failure_count": 0,
        "timeout_count": 0,
        "skip_count": 0,
    }
    (args.output_root / "runtime_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
