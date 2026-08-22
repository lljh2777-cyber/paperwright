#!/usr/bin/env python3
"""Prepare dependency-free blind review applications from an immutable E7 run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from paperwright.grobid_evaluation import (
    build_grobid_audit_task,
    canonical_grobid_evaluation_json,
)
from paperwright.grobid_human_review import (
    GROBID_HUMAN_REVIEW_MANIFEST_VERSION,
    build_grobid_human_review_template,
    grobid_audit_task_sha256,
    render_grobid_human_review_html,
    render_grobid_human_review_index,
    validate_grobid_audit_task,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_run_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RuntimeError("audit page image path is invalid")
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise RuntimeError("audit page image path escapes run root")
    return result


def _instructions(run_root: Path, output: Path) -> str:
    relative = output.relative_to(run_root).as_posix()
    return f"""# GROBID Gold Review

This directory is a blind, offline annotation package. It exposes GROBID claims,
their PDF coordinates, aligned PDFium native text and alignment scores. It does
not expose downstream Recipe adoption or model decisions.

## Open

Directly open `index.html`, or serve the immutable run root locally:

```bash
python3 -m http.server 8765 --directory {run_root}
```

Then open `http://127.0.0.1:8765/{relative}/index.html`.

## Annotate

1. Enter the reviewer name.
2. Label every claim as `correct`, `partial`, `wrong_role`, `unsupported`, or
   `uncertain`. Keyboard shortcuts 1–5 select those labels; left/right arrows
   move between claims.
3. In **Gold units**, independently enumerate title, abstract, section heading,
   Figure/Table caption and reference units from the PDF. Do not infer missing
   units from the GROBID claim list. One semantic unit may contain multiple page
   fragments; append a continuation to the existing unit instead of creating a
   second unit.
4. Mark a gold type `complete` only after checking the whole paper, or
   `not_applicable` only when the paper truly contains none.
5. Export JSON after every session. Browser autosave is only a convenience;
   exported JSON is the review artifact.

Validate a saved response from the repository root:

```bash
PYTHONPATH=src .venv/bin/python tools/validate_grobid_human_review.py \\
  {output}/tasks/DOCUMENT.json \\
  /path/to/DOCUMENT.human-review.json
```

Add `--require-complete` only for final scoring submission.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    report_path = run_root / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = (
        args.output.resolve()
        if args.output is not None
        else run_root / "human-review-v0.2"
    )
    if output.exists():
        raise SystemExit(f"human review output 已存在，拒绝覆盖: {output}")

    prepared: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    for document in report.get("documents", []):
        if document.get("status") != "complete":
            continue
        document_id = document["document_id"]
        review_root = run_root / document_id / "grobid-crf"
        task = build_grobid_audit_task(
            review_root,
            document_id=document_id,
            source_sha256=document["source_sha256"],
            page_image_path_prefix=f"{document_id}/grobid-crf",
        )
        validate_grobid_audit_task(task)
        task_hash = grobid_audit_task_sha256(task)
        response = build_grobid_human_review_template(task)
        image_sources: dict[str, str] = {}
        for page in task["page_images"]:
            source = _safe_run_path(run_root, page["path"])
            if not source.is_file() or _sha256(source) != page["sha256"]:
                raise RuntimeError(f"audit page image missing or changed: {source}")
            image_sources[str(page["page_index"])] = Path(
                os.path.relpath(source, output)
            ).as_posix()
        html_payload = render_grobid_human_review_html(
            task,
            response,
            image_sources=image_sources,
        )
        task_payload = canonical_grobid_evaluation_json(task)
        response_payload = canonical_grobid_evaluation_json(response)
        claim_types = sorted({claim["claim_type"] for claim in task["claims"]})
        prepared.append(
            {
                "document_id": document_id,
                "task_payload": task_payload,
                "response_payload": response_payload,
                "html_payload": html_payload,
                "task_sha256": task_hash,
                "html_sha256": hashlib.sha256(
                    html_payload.encode("utf-8")
                ).hexdigest(),
                "claim_count": task["claim_count"],
                "claim_types": claim_types,
            }
        )
        index_rows.append(
            {
                "document_id": document_id,
                "html": f"{document_id}.html",
                "claim_count": task["claim_count"],
                "claim_types": claim_types,
            }
        )

    if not prepared:
        raise SystemExit("evaluation run contains no complete document pairs")
    manifest = {
        "contract_version": GROBID_HUMAN_REVIEW_MANIFEST_VERSION,
        "source_report": {
            "path": "../report.json",
            "sha256": _sha256(report_path),
        },
        "document_count": len(prepared),
        "claim_count": sum(item["claim_count"] for item in prepared),
        "downstream_adoption_disclosed": False,
        "documents": [
            {
                "document_id": item["document_id"],
                "claim_count": item["claim_count"],
                "claim_types": item["claim_types"],
                "task": {
                    "path": f"tasks/{item['document_id']}.json",
                    "sha256": item["task_sha256"],
                },
                "response_template": {
                    "path": f"response-templates/{item['document_id']}.json",
                    "sha256": hashlib.sha256(
                        item["response_payload"].encode("utf-8")
                    ).hexdigest(),
                },
                "html": {
                    "path": f"{item['document_id']}.html",
                    "sha256": item["html_sha256"],
                },
            }
            for item in prepared
        ],
    }
    index_payload = render_grobid_human_review_index(index_rows)
    output.mkdir(parents=True)
    (output / "tasks").mkdir()
    (output / "response-templates").mkdir()
    for item in prepared:
        document_id = item["document_id"]
        (output / "tasks" / f"{document_id}.json").write_text(
            item["task_payload"], encoding="utf-8", newline="\n"
        )
        (output / "response-templates" / f"{document_id}.json").write_text(
            item["response_payload"], encoding="utf-8", newline="\n"
        )
        (output / f"{document_id}.html").write_text(
            item["html_payload"], encoding="utf-8", newline="\n"
        )
    (output / "index.html").write_text(
        index_payload, encoding="utf-8", newline="\n"
    )
    (output / "README.md").write_text(
        _instructions(run_root, output), encoding="utf-8", newline="\n"
    )
    manifest_payload = canonical_grobid_evaluation_json(manifest)
    (output / "manifest.json").write_text(
        manifest_payload, encoding="utf-8", newline="\n"
    )
    print(manifest_payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
