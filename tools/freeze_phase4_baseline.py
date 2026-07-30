#!/usr/bin/env python3
"""Record product source hashes from the authorized immutable base commit."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "ee379a5be6c713012e721d08995a88d5abec19af"


def git(*args: str) -> bytes:
    process = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode(errors="replace"))
    return process.stdout


def main() -> int:
    if git("rev-parse", "HEAD").decode().strip() != BASE:
        raise RuntimeError("HEAD does not match the authorized Phase 3 base")
    paths = [
        item
        for item in git("ls-tree", "-r", "--name-only", BASE).decode().splitlines()
        if item.startswith(("src/", "config/"))
        or item in {"pyproject.toml", "README.md", "REPRODUCE.md"}
    ]
    records = []
    for path in paths:
        data = git("show", f"{BASE}:{path}")
        records.append(
            {
                "path": path,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    output = {
        "schema_version": "paper2md-phase4-render-spike-baseline-v1",
        "base_commit": BASE,
        "file_count": len(records),
        "total_bytes": sum(item["size_bytes"] for item in records),
        "files": records,
    }
    target = ROOT / "phase4_render_spike/baseline_source_hashes.json"
    target.write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
