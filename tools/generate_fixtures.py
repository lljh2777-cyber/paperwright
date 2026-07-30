#!/usr/bin/env python3
"""Deterministically generate/check project-authored bootstrap fixtures."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import minimal_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "tests/fixtures/physical_document.minimal.json"
    expected = minimal_document().canonical_json()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != expected:
            print("fixture 与确定性生成结果不一致", file=sys.stderr)
            return 1
        print("fixture check: PASS")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
