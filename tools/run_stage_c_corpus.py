#!/usr/bin/env python3
"""Run the frozen Stage C corpus without putting PDF payloads in the repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "realworld" / "oa_sources.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--paper", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.output_root.exists():
        raise RuntimeError("output root already exists; refusing to overwrite")
    args.output_root.mkdir(parents=True)
    log_root = args.output_root / "_logs"
    log_root.mkdir()
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    wanted = set(args.paper)
    selected = [
        paper
        for paper in sources["papers"]
        if not wanted or paper["id"] in wanted
    ]
    if wanted and wanted != {paper["id"] for paper in selected}:
        raise RuntimeError("unknown paper ID requested")

    records: list[dict[str, object]] = []
    for paper in selected:
        paper_id = paper["id"]
        source = args.pdf_root / f"{paper_id}.pdf"
        if not source.is_file():
            raise RuntimeError(f"missing input {source}")
        actual_hash = sha256(source)
        if actual_hash != paper["sha256"]:
            raise RuntimeError(f"{paper_id} input hash mismatch")
        destination = args.output_root / paper_id
        command = [
            sys.executable,
            "-m",
            "paper2md",
            "convert",
            str(source),
            str(destination),
            "--workspace-root",
            str(args.output_root.parent),
        ]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment["PYTHONUTF8"] = "1"
        environment["LC_ALL"] = "C.UTF-8"
        environment["TZ"] = "UTC"
        started = utc_now()
        timeout = False
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
            )
            exit_code = process.returncode
            stdout = process.stdout
            stderr = process.stderr
        except subprocess.TimeoutExpired as exc:
            timeout = True
            exit_code = 124
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        ended = utc_now()
        stdout_path = log_root / f"{paper_id}.stdout.txt"
        stderr_path = log_root / f"{paper_id}.stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        output_files = []
        if destination.exists():
            for path in sorted(item for item in destination.rglob("*") if item.is_file()):
                output_files.append(
                    {
                        "path": path.relative_to(destination).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )
        records.append(
            {
                "paper_id": paper_id,
                "run_label": args.run_label,
                "command_argv": command,
                "started_at_utc": started,
                "ended_at_utc": ended,
                "exit_code": exit_code,
                "timeout": timeout,
                "input_sha256": actual_hash,
                "stdout": {
                    "path": stdout_path.relative_to(args.output_root).as_posix(),
                    "size_bytes": stdout_path.stat().st_size,
                    "sha256": sha256(stdout_path),
                },
                "stderr": {
                    "path": stderr_path.relative_to(args.output_root).as_posix(),
                    "size_bytes": stderr_path.stat().st_size,
                    "sha256": sha256(stderr_path),
                },
                "output_file_count": len(output_files),
                "output_size_bytes": sum(
                    int(item["size_bytes"]) for item in output_files
                ),
                "outputs": output_files,
            }
        )
    summary = {
        "schema_version": "paper2md-v2-realworld-run-v1",
        "run_label": args.run_label,
        "source_manifest_sha256": sha256(SOURCES),
        "paper_count": len(selected),
        "records": records,
        "success_count": sum(item["exit_code"] == 0 for item in records),
        "failure_count": sum(item["exit_code"] != 0 for item in records),
        "timeout_count": sum(bool(item["timeout"]) for item in records),
        "skip_count": 0,
    }
    summary_path = args.output_root / "_run_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "paper_count": len(selected),
                "success_count": summary["success_count"],
                "failure_count": summary["failure_count"],
                "timeout_count": summary["timeout_count"],
                "output": str(args.output_root),
            },
            sort_keys=True,
        )
    )
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
