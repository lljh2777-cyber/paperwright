#!/usr/bin/env python3
"""Independently recompute Stage C content and traceability statistics."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image
import pypdfium2 as pdfium

from paper2md.manifest import sha256_file, validate_manifest
from paper2md.models import PhysicalDocument

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "realworld" / "oa_sources.json"
OUTPUT = ROOT / "realworld" / "realworld_summary.json"
DETERMINISM_IDS = ("RW2-001", "RW2-003", "RW2-005", "RW2-007")


def normalized(value: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFC", value)
        if character.isalnum()
    )


def multiset_recall(reference: str, observed: str) -> float:
    expected = Counter(reference)
    actual = Counter(observed)
    return sum((expected & actual).values()) / max(1, sum(expected.values()))


def pdf_text_pages(path: Path) -> list[str]:
    document = pdfium.PdfDocument(path)
    result = []
    try:
        for index in range(len(document)):
            page = document[index]
            text_page = page.get_textpage()
            try:
                result.append(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    return result


def inventory(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, sha256_file(path))
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--final-root", type=Path, required=True)
    parser.add_argument("--determinism-root", type=Path, required=True)
    args = parser.parse_args()
    if OUTPUT.exists():
        raise RuntimeError("refusing to overwrite realworld summary")
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    papers = []
    failures: list[dict[str, object]] = []
    for source_record in sources["papers"]:
        paper_id = source_record["id"]
        pdf_path = args.pdf_root / f"{paper_id}.pdf"
        output = args.final_root / paper_id
        baseline = args.baseline_root / paper_id
        manifest_path = output / "manifest.json"
        article_path = output / "article.md"
        physical_path = output / "physical_document.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)
        physical = PhysicalDocument.from_dict(
            json.loads(physical_path.read_text(encoding="utf-8"))
        )
        article = article_path.read_text(encoding="utf-8")
        baseline_title = (
            (baseline / "article.md").read_text(encoding="utf-8").splitlines()[0][2:]
        )
        final_title = article.splitlines()[0][2:]
        reference_pages = [normalized(value) for value in pdf_text_pages(pdf_path)]
        extracted_pages = [
            normalized(
                " ".join(
                    element.text or ""
                    for element in page.elements
                    if element.kind == "text"
                )
            )
            for page in physical.pages
        ]
        page_recalls = [
            multiset_recall(reference, observed)
            for reference, observed in zip(reference_pages, extracted_pages)
        ]
        missing_nonempty_pages = [
            index + 1
            for index, (reference, observed) in enumerate(
                zip(reference_pages, extracted_pages)
            )
            if len(reference) >= 20 and not observed
        ]
        outputs_valid = True
        for record in manifest["outputs"]:
            path = output / record["path"]
            if (
                not path.is_file()
                or path.stat().st_size != record["size_bytes"]
                or sha256_file(path) != record["sha256"]
            ):
                outputs_valid = False
        images = []
        for record in manifest["images"]:
            path = output / record["path"]
            markdown_reference = f"]({record['path']})" in article
            with Image.open(path) as image:
                image.load()
                observed_format = image.format
                observed_size = list(image.size)
                extrema = image.convert("RGB").getextrema()
                nonblank = any(low != high for low, high in extrema)
            valid = (
                markdown_reference
                and sha256_file(path) == record["sha256"]
                and path.stat().st_size == record["size_bytes"]
                and observed_size
                == [record["width_px"], record["height_px"]]
                and observed_format == "PNG"
                and nonblank
            )
            images.append(
                {
                    "path": record["path"],
                    "page": record["page"],
                    "size_bytes": record["size_bytes"],
                    "sha256": record["sha256"],
                    "dimensions": observed_size,
                    "markdown_reference": markdown_reference,
                    "nonblank_pixel_range": nonblank,
                    "valid": valid,
                }
            )
        pipe_grid_lines = [
            line
            for line in article.splitlines()
            if re.fullmatch(r"\s*\|(?:[^|]*\|){2,}\s*", line)
        ]
        element_count = sum(len(page.elements) for page in physical.pages)
        kind_counts = Counter(
            element.kind for page in physical.pages for element in page.elements
        )
        deterministic = None
        deterministic_mismatches: list[str] = []
        if paper_id in DETERMINISM_IDS:
            first = inventory(output)
            second = inventory(args.determinism_root / paper_id)
            deterministic = first == second
            deterministic_mismatches = sorted(
                key
                for key in set(first) | set(second)
                if first.get(key) != second.get(key)
            )
        paper_inventory = inventory(output)
        record = {
            "paper_id": paper_id,
            "title_expected": source_record["title"],
            "title_baseline": baseline_title,
            "title_final": final_title,
            "title_exact_normalized": normalized(final_title)
            == normalized(source_record["title"]),
            "title_changed": baseline_title != final_title,
            "source_sha256_match": sha256_file(pdf_path) == source_record["sha256"],
            "page_count_expected": source_record["page_count"],
            "page_count_actual": len(physical.pages),
            "status": manifest["status"],
            "manifest_valid": True,
            "physical_document_valid": True,
            "output_hashes_valid": outputs_valid,
            "element_count": element_count,
            "element_kind_counts": dict(sorted(kind_counts.items())),
            "reference_normalized_character_count": sum(map(len, reference_pages)),
            "extracted_normalized_character_count": sum(map(len, extracted_pages)),
            "character_multiset_recall_macro": sum(page_recalls)
            / len(page_recalls),
            "character_multiset_recall_min_page": min(page_recalls),
            "character_multiset_recall_by_page": page_recalls,
            "missing_nonempty_pages": missing_nonempty_pages,
            "image_count": len(images),
            "valid_image_count": sum(item["valid"] for item in images),
            "images": images,
            "degraded_table_page_count": len(manifest["degraded"]),
            "degraded_table_pages": [
                item["page"] for item in manifest["degraded"]
            ],
            "fabricated_markdown_table_grid_lines": len(pipe_grid_lines),
            "warning_count": len(manifest["warnings"]),
            "warning_codes": dict(
                sorted(Counter(item["code"] for item in manifest["warnings"]).items())
            ),
            "output_file_count": len(paper_inventory),
            "output_size_bytes": sum(size for size, _ in paper_inventory.values()),
            "determinism_evaluated": deterministic is not None,
            "deterministic": deterministic,
            "determinism_mismatches": deterministic_mismatches,
        }
        papers.append(record)
        blocking_checks = {
            "source_hash": record["source_sha256_match"],
            "page_count": record["page_count_expected"]
            == record["page_count_actual"],
            "title": record["title_exact_normalized"],
            "manifest": record["manifest_valid"] and outputs_valid,
            "no_missing_nonempty_page": not missing_nonempty_pages,
            "image_integrity": record["valid_image_count"] == record["image_count"],
            "honest_table_degradation": not pipe_grid_lines,
            "determinism": deterministic is not False,
        }
        for check_id, passed in blocking_checks.items():
            if not passed:
                failures.append({"paper_id": paper_id, "check_id": check_id})

    summary = {
        "schema_version": "paper2md-v2-realworld-summary-v1",
        "base_commit": "0897f3ca82b74468ece7aa65d6e331416c4afd96",
        "source_manifest": {
            "path": "realworld/oa_sources.json",
            "sha256": sha256_file(SOURCES),
        },
        "scope": {
            "paper_count": 8,
            "page_count": 138,
            "new_corpus_not_historical_recovery": True,
            "pdf_payloads_in_source_delivery": False,
        },
        "papers": papers,
        "totals": {
            "success_count": sum(
                paper["status"] in {"success", "success_with_degradation"}
                for paper in papers
            ),
            "failure_count": len(failures),
            "skip_count": 0,
            "title_exact_normalized": sum(
                paper["title_exact_normalized"] for paper in papers
            ),
            "no_missing_nonempty_page": sum(
                not paper["missing_nonempty_pages"] for paper in papers
            ),
            "image_count": sum(paper["image_count"] for paper in papers),
            "valid_image_count": sum(
                paper["valid_image_count"] for paper in papers
            ),
            "degraded_table_page_count": sum(
                paper["degraded_table_page_count"] for paper in papers
            ),
            "fabricated_markdown_table_grid_lines": sum(
                paper["fabricated_markdown_table_grid_lines"] for paper in papers
            ),
            "output_file_count": sum(
                paper["output_file_count"] for paper in papers
            ),
            "output_size_bytes": sum(
                paper["output_size_bytes"] for paper in papers
            ),
            "determinism_paper_count": sum(
                paper["determinism_evaluated"] for paper in papers
            ),
            "determinism_pass_count": sum(
                paper["deterministic"] is True for paper in papers
            ),
            "character_multiset_recall_macro": sum(
                paper["character_multiset_recall_macro"] for paper in papers
            )
            / len(papers),
            "character_multiset_recall_min_page": min(
                paper["character_multiset_recall_min_page"] for paper in papers
            ),
        },
        "failures": failures,
        "not_verified": [
            "real publisher generalization beyond these eight OA PDFs",
            "semantic table reconstruction",
            "caption adjacency",
            "complete reconstruction of vector and fragmented multi-panel figures",
            "OCR/scanned documents",
            "formula-to-LaTeX fidelity",
            "release redistribution approval including agg23 NOASSERTION",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "failures": len(failures),
                **summary["totals"],
            },
            sort_keys=True,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
