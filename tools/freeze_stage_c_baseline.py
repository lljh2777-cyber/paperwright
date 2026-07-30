#!/usr/bin/env python3
"""Freeze exact Stage B source identities before observing Stage C outputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "0897f3ca82b74468ece7aa65d6e331416c4afd96"
OUTPUT = ROOT / "realworld" / "baseline_source_hashes.json"


def git_bytes(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite {OUTPUT}")
    head = git_bytes("rev-parse", "HEAD").decode().strip()
    if head != BASE:
        raise RuntimeError(f"expected {BASE}, got {head}")
    entries: list[dict[str, object]] = []
    listing = git_bytes("ls-tree", "-r", "-z", "--full-tree", BASE)
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = raw_path.decode("utf-8")
        payload = git_bytes("show", f"{BASE}:{path}")
        entries.append(
            {
                "path": path,
                "git_mode": mode,
                "git_object": object_id,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    import pypdfium2 as pdfium
    import pypdfium2_raw

    runtime_dir = Path(pypdfium2_raw.__file__).resolve().parent
    runtime_candidates = sorted(
        item
        for item in runtime_dir.iterdir()
        if item.is_file()
        and (
            item.name.startswith("libpdfium.")
            or item.name.casefold() == "pdfium.dll"
        )
    )
    runtime = runtime_candidates[0] if len(runtime_candidates) == 1 else None
    output = {
        "schema_version": "paper2md-v2-realworld-baseline-v1",
        "frozen_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "base_commit": BASE,
        "git_tree": git_bytes("rev-parse", f"{BASE}^{{tree}}").decode().strip(),
        "tracked_file_count": len(entries),
        "tracked_files": entries,
        "environment": {
            "python": sys.version,
            "pypdfium2": importlib.metadata.version("pypdfium2"),
            "pdfium": str(pdfium.PDFIUM_INFO),
            "pillow": importlib.metadata.version("Pillow"),
            "pdfium_runtime": (
                {
                    "filename": runtime.name,
                    "size_bytes": runtime.stat().st_size,
                    "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                }
                if runtime
                else {
                    "filename": None,
                    "candidate_count": len(runtime_candidates),
                    "sha256": None,
                }
            ),
        },
        "stage_c_code_observed": False,
        "stage_c_backend_run_observed": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "files": len(entries)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
