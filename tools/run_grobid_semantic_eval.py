#!/usr/bin/env python3
"""Run the preregistered native/GROBID evidence comparison."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib import request

from paperwright.grobid_evaluation import (
    GROBID_EVAL_REPORT_VERSION,
    aggregate_grobid_evidence_summaries,
    build_grobid_audit_task,
    canonical_grobid_evaluation_json,
    compare_grobid_review_summaries,
    summarize_grobid_review,
    validate_grobid_evaluation_corpus,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_json(url: str) -> dict[str, Any]:
    with request.urlopen(url, timeout=10) as response:  # noqa: S310
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"GROBID endpoint did not return an object: {url}")
    return value


def _grobid_health(base_url: str, expected_version: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    version = _get_json(f"{base}/api/version")
    health = _get_json(f"{base}/api/health")
    actual = version.get("version")
    if actual != expected_version:
        raise RuntimeError(
            f"GROBID version mismatch: expected {expected_version}, got {actual}"
        )
    if health.get("ready") is not True:
        raise RuntimeError("GROBID health endpoint is not ready")
    models = health.get("models", {})
    if models.get("totalFailed") != 0:
        raise RuntimeError("GROBID reports failed models")
    return {
        "base_url": base,
        "version": actual,
        "ready": True,
        "loaded_model_count": models.get("totalLoaded"),
        "failed_model_count": models.get("totalFailed"),
    }


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _run_branch(
    source: Path,
    output: Path,
    log_root: Path,
    *,
    grobid_url: str | None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("PAPERWRIGHT_GROBID_URL", None)
    env.pop("PAPERWRIGHT_DOCLING_ENABLED", None)
    if grobid_url is not None:
        env["PAPERWRIGHT_GROBID_URL"] = grobid_url
    command = [
        sys.executable,
        "-m",
        "paperwright",
        "layout-prepare",
        str(source),
        str(output),
        "--extraction-profile",
        "standard",
    ]
    completed = subprocess.run(  # noqa: S603
        command,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    _write(log_root.with_suffix(".stdout.log"), completed.stdout)
    _write(log_root.with_suffix(".stderr.log"), completed.stderr)
    return {
        "status": "complete" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "command": command,
        "grobid_enabled": grobid_url is not None,
    }


def _aggregate(documents: list[dict[str, Any]]) -> dict[str, Any]:
    provider_statuses: Counter[str] = Counter()
    claims: Counter[str] = Counter()
    failures = 0
    for document in documents:
        if document["status"] != "complete":
            failures += 1
            continue
        summary = document["grobid_crf"]["evidence_summary"]
        provider_statuses[str(summary["provider_status"])] += 1
        claims.update(summary["claim_counts_by_type"])
    return {
        "document_count": len(documents),
        "complete_pair_count": len(documents) - failures,
        "failed_pair_count": failures,
        "grobid_provider_statuses": dict(sorted(provider_statuses.items())),
        "grobid_claim_counts_by_type": dict(sorted(claims.items())),
        "by_claim_type": aggregate_grobid_evidence_summaries([
            document["grobid_crf"]["evidence_summary"]
            for document in documents
            if document["status"] == "complete"
        ]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--grobid-url", default="http://127.0.0.1:8070")
    parser.add_argument("--grobid-version", default="0.9.0")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    corpus, corpus_root = validate_grobid_evaluation_corpus(args.corpus)
    if args.validate_only:
        print(
            canonical_grobid_evaluation_json(
                {
                    "status": "valid",
                    "document_count": len(corpus["documents"]),
                    "corpus_sha256": _sha256(args.corpus.resolve()),
                }
            ),
            end="",
        )
        return 0
    if args.output is None:
        parser.error("output is required unless --validate-only is used")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"evaluation output 已存在，拒绝覆盖: {output}")
    health = _grobid_health(args.grobid_url, args.grobid_version)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    audit_root = output / "audit-tasks"
    audit_root.mkdir()
    document_results = []
    for document in corpus["documents"]:
        document_id = document["document_id"]
        source = (corpus_root / document["file"]).resolve()
        document_root = output / document_id
        document_root.mkdir()
        native_output = document_root / "native"
        grobid_output = document_root / "grobid-crf"
        native_run = _run_branch(
            source,
            native_output,
            document_root / "native",
            grobid_url=None,
        )
        grobid_run = _run_branch(
            source,
            grobid_output,
            document_root / "grobid-crf",
            grobid_url=health["base_url"],
        )
        result: dict[str, Any] = {
            "document_id": document_id,
            "source_sha256": document["sha256"],
            "native": native_run,
            "grobid_crf": grobid_run,
        }
        if native_run["status"] == "complete":
            native_run["evidence_summary"] = summarize_grobid_review(native_output)
        if grobid_run["status"] == "complete":
            grobid_run["evidence_summary"] = summarize_grobid_review(grobid_output)
            task = build_grobid_audit_task(
                grobid_output,
                document_id=document_id,
                source_sha256=document["sha256"],
                page_image_path_prefix=f"{document_id}/grobid-crf",
            )
            task_path = audit_root / f"{document_id}.json"
            _write(task_path, canonical_grobid_evaluation_json(task))
            grobid_run["audit_task"] = {
                "path": str(task_path.relative_to(output)),
                "sha256": _sha256(task_path),
            }
        if native_run["status"] == grobid_run["status"] == "complete":
            result["comparison"] = compare_grobid_review_summaries(
                native_run["evidence_summary"],
                grobid_run["evidence_summary"],
            )
        result["status"] = (
            "complete"
            if native_run["status"] == grobid_run["status"] == "complete"
            else "failed"
        )
        document_results.append(result)
    report = {
        "contract_version": GROBID_EVAL_REPORT_VERSION,
        "corpus": {
            "path": str(args.corpus.resolve()),
            "sha256": _sha256(args.corpus.resolve()),
            "baseline_commit": corpus.get("baseline_commit"),
            "protocol_commit": corpus.get("protocol_commit"),
        },
        "grobid": health,
        "documents": document_results,
        "summary": _aggregate(document_results),
    }
    _write(output / "report.json", canonical_grobid_evaluation_json(report))
    print(canonical_grobid_evaluation_json(report["summary"]), end="")
    return 1 if report["summary"]["failed_pair_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
