#!/usr/bin/env python3
"""Validate and summarize an external caption/visual relation dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperwright.relation_dataset import (
    relation_dataset_summary,
    validate_relation_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    value = json.loads(args.dataset.read_text(encoding="utf-8"))
    validate_relation_dataset(value)
    summary = relation_dataset_summary(value)
    payload = json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if args.summary is not None:
        if args.summary.exists():
            raise SystemExit(f"summary 已存在，拒绝覆盖: {args.summary}")
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
