#!/usr/bin/env python3
"""L3 program-synthesis bridge (thin model adapter for join-blocks).

The deterministic kernel lives in ``paperwright.synthesize``: restricted-DSL
validation, the read-only ReviewAPI, execution limits, word-bag conservation
and L1-compatible review construction.  This tool only owns model calls,
prompt building and the self-repair loop, so PaperWright's core keeps its
no-network, no-LLM boundary.

Usage:
    export DASHSCOPE_API_KEY=...
    PYTHONPATH=src python tools/run_text_synthesize.py \
        article-model.json text-task.json text-review.json [--dry-run]

The produced text-review.json is identical in contract to the L1 bridge and
must pass `paperwright validate-text-review` before anything is applied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from paperwright.exceptions import ContractValidationError
from paperwright.synthesize import (
    ConservationError,
    DSLValidationError,
    ReviewAPI,
    build_synthesis_review,
    enrich_task_blocks,
    execute_dsl,
)

MODEL = os.environ.get("PW_SYNTH_MODEL", "qwen3.7-plus")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

_REPAIRABLE_ERRORS = (
    DSLValidationError,
    ConservationError,
    ContractValidationError,
    SyntaxError,
    NameError,
    AttributeError,
    TypeError,
    ValueError,
    TimeoutError,
    RuntimeError,
)


def _load_client():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("缺少可选依赖 openai：pip install openai") from exc
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        key_file = Path.home() / ".dashscope_key"
        if key_file.is_file():
            key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("未设置 DASHSCOPE_API_KEY")
    return OpenAI(api_key=key, base_url=BASE_URL)


def _candidate_summary(api: ReviewAPI) -> str:
    lines = []
    for i, (a, b) in enumerate(api.adjacent_body_pairs()):
        col = api.same_column(a, b)
        gap = api.vertical_gap(a, b)
        gap_s = f"{gap:.4f}" if gap is not None else "n/a"
        lines.append(
            f"[{i}] col={col} gap={gap_s} | prev: ...{a['markdown'][-45:]!r} | next: {b['markdown'][:45:]!r}"
        )
    return "\n".join(lines) if lines else "(no candidates)"


def _build_prompt(api: ReviewAPI) -> str:
    return (
        "You are writing a small, restricted Python script that decides which adjacent "
        "body text blocks in a scientific paper should be joined into one paragraph.\n\n"
        "Available read-only object `api`:\n"
        "- api.blocks() -> list of blocks (fields: id, kind, order, page, markdown, "
        "bbox or None; access via either block.markdown or block[\"markdown\"])\n"
        "- api.body_blocks() -> list of body blocks\n"
        "- api.adjacent_body_pairs() -> list of (prev, curr) pairs meeting all hard "
        "preconditions (same page, order-adjacent, editable, unrelated, no terminal "
        "punctuation, curr lowercase)\n"
        "- api.same_column(a, b) -> bool (bboxes overlap horizontally)\n"
        "- api.vertical_gap(a, b) -> float or None (vertical distance, negative = overlap)\n"
        "- api.word_bag(text) -> dict of normalized word counts\n"
        "- api.emit_join(prev_id, curr_id, reason) -> declare a join (never rewrite text)\n\n"
        "Restrictions:\n"
        "- Top-level script only: NO function/class definitions, NO imports, NO while, "
        "NO try/except, NO private or dunder attributes, NO print/type/getattr.\n"
        "- Only call api.* methods and these builtins: len, range, sorted, min, max, abs, "
        "round, sum, any, all, enumerate, zip, int, float, str, bool, list, dict, tuple, set.\n\n"
        "Join only pairs that are GENUINELY the same paragraph split by the extractor. "
        "Use geometric evidence (same column + small vertical gap) where bbox exists; "
        "author name/affiliation/email lines and heading+body are NOT joins. "
        "When unsure, do NOT emit.\n\n"
        "Candidate pairs (index, geometry, text):\n"
        f"{_candidate_summary(api)}\n\n"
        "Output ONLY the script, no explanation, no markdown fences."
    )


def _generate_script(client, api: ReviewAPI) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": _build_prompt(api)}],
        temperature=0,
        max_tokens=2048,
        extra_body={"enable_thinking": False},
    )
    return _strip_fences(resp.choices[0].message.content or "")


def _repair_script(client, api: ReviewAPI, code: str, error: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{_build_prompt(api)}\n\n"
                    f"Your previous script raised this error:\n{error}\n\n"
                    f"Previous script:\n{code}\n\n"
                    "Rewrite the script to fix the error. Output ONLY the corrected "
                    "script, no explanation, no markdown fences."
                ),
            }
        ],
        temperature=0,
        max_tokens=2048,
        extra_body={"enable_thinking": False},
    )
    return _strip_fences(resp.choices[0].message.content or "")


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code)
    return code


def _synthesize(client, api: ReviewAPI, task: dict, attempts: int = 3) -> tuple[str, dict]:
    code = _generate_script(client, api)
    for _ in range(attempts):
        print(f"=== 尝试生成的 DSL 脚本 ===\n{code}\n=== 结束 ===", file=sys.stderr)
        try:
            operations = execute_dsl(code, api)
            review = build_synthesis_review(task, operations)
            return code, review
        except _REPAIRABLE_ERRORS as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            code = _repair_script(client, api, code, str(exc))
    raise SystemExit(f"L3 代码生成在 {attempts} 轮内未能产出可执行且通过校验的脚本")


def _run_script(code: str, api: ReviewAPI, task: dict) -> dict:
    try:
        operations = execute_dsl(code, api)
    except _REPAIRABLE_ERRORS as exc:
        raise SystemExit(f"DSL 脚本执行失败: {exc}") from exc
    return build_synthesis_review(task, operations)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("article_model_json", type=Path)
    ap.add_argument("task_json", type=Path)
    ap.add_argument("review_json", type=Path)
    ap.add_argument("--script", type=Path, help="跳过模型，直接执行已有 DSL 脚本")
    ap.add_argument("--dry-run", action="store_true", help="只生成脚本不执行")
    args = ap.parse_args()

    article_model = json.loads(args.article_model_json.read_text(encoding="utf-8"))
    task = json.loads(args.task_json.read_text(encoding="utf-8"))
    blocks = enrich_task_blocks(task, article_model)
    join_allowed = "join-blocks" in task["policy"].get("allowed_operations", ())
    api = ReviewAPI(blocks, join_allowed=join_allowed)

    if args.script:
        code = args.script.read_text(encoding="utf-8")
        if args.dry_run:
            print(code)
            return 0
        review = _run_script(code, api, task)
    else:
        client = _load_client()
        if args.dry_run:
            print(_generate_script(client, api))
            return 0
        _, review = _synthesize(client, api, task)

    if args.review_json.exists():
        raise SystemExit(f"输出文件已存在，拒绝覆盖: {args.review_json}")
    args.review_json.write_text(
        json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"守恒校验与 validate-text-review 通过，已写 {args.review_json.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
