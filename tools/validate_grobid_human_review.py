#!/usr/bin/env python3
"""Validate one task-bound GROBID human-review response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperwright.grobid_evaluation import canonical_grobid_evaluation_json
from paperwright.grobid_human_review import validate_grobid_human_review


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON 顶层必须是 object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", type=Path)
    parser.add_argument("response", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    task = _load(args.task.resolve())
    response = _load(args.response.resolve())
    completion = validate_grobid_human_review(
        task,
        response,
        require_complete=args.require_complete,
    )
    print(
        canonical_grobid_evaluation_json(
            {
                "status": "valid",
                "document_id": response["document_id"],
                "completion": completion,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
