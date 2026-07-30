#!/usr/bin/env python3
"""Record the immutable Stage C source/runtime baseline for Phase 3."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2_raw
from PIL import __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
BASE = "8ecd01871eff02e700f0cef1c64cae186be8c69f"


def _git(*args: str, binary: bool = False):
    process = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=not binary,
    )
    return process.stdout


def main() -> int:
    paths = [
        item
        for item in _git("ls-tree", "-r", "--name-only", BASE).splitlines()
        if item.startswith(("src/", "config/"))
        or item in {"pyproject.toml", "README.md", "REPRODUCE.md"}
    ]
    files = []
    for path in paths:
        data = _git("show", f"{BASE}:{path}", binary=True)
        files.append(
            {
                "path": path,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    package_dir = Path(pypdfium2_raw.__file__).resolve().parent
    binaries = sorted(
        item
        for item in package_dir.iterdir()
        if item.is_file()
        and (
            item.name.startswith("libpdfium.")
            or item.name.casefold() == "pdfium.dll"
        )
    )
    value = {
        "baseline_version": "paper2md-phase3-stage-c-source-baseline-v1",
        "base_commit": BASE,
        "base_commit_verified": _git("rev-parse", "HEAD").strip() == BASE,
        "files": files,
        "runtime": {
            "python": sys.version.split()[0],
            "pypdfium2": importlib.metadata.version("pypdfium2"),
            "pdfium": str(pdfium.PDFIUM_INFO),
            "pillow": pillow_version,
            "pdfium_binary": (
                {
                    "name": binaries[0].name,
                    "size_bytes": binaries[0].stat().st_size,
                    "sha256": hashlib.sha256(binaries[0].read_bytes()).hexdigest(),
                }
                if len(binaries) == 1
                else None
            ),
        },
    }
    target = ROOT / "phase3/baseline_source_hashes.json"
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
