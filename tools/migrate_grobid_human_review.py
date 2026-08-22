#!/usr/bin/env python3
"""Migrate a GROBID human review to the multi-page gold-unit contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperwright.grobid_evaluation import canonical_grobid_evaluation_json
from paperwright.grobid_human_review import (
    merge_grobid_gold_units,
    migrate_grobid_human_review_v01,
    validate_grobid_human_review,
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON 顶层必须是 object: {path}")
    return value


def _merge_pair(value: str) -> tuple[str, str]:
    source, separator, target = value.partition("=")
    if not separator or not source or not target:
        raise argparse.ArgumentTypeError("merge 必须为 SOURCE_UNIT_ID=TARGET_UNIT_ID")
    return source, target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", type=Path)
    parser.add_argument("response", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--merge-gold",
        action="append",
        default=[],
        type=_merge_pair,
        metavar="SOURCE=TARGET",
        help="merge a continued source unit into its semantic target",
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"迁移输出已存在，拒绝覆盖: {output}")
    task = _load(args.task.resolve())
    migrated = migrate_grobid_human_review_v01(
        task,
        _load(args.response.resolve()),
    )
    for source, target in args.merge_gold:
        migrated = merge_grobid_gold_units(
            task,
            migrated,
            source_unit_id=source,
            target_unit_id=target,
        )
    completion = validate_grobid_human_review(
        task,
        migrated,
        require_complete=args.require_complete,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        canonical_grobid_evaluation_json(migrated),
        encoding="utf-8",
        newline="\n",
    )
    print(
        canonical_grobid_evaluation_json(
            {
                "status": "migrated",
                "document_id": migrated["document_id"],
                "output": str(output),
                "completion": completion,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
