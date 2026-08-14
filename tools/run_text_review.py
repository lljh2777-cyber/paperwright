#!/usr/bin/env python3
"""Pure-text review bridge: model judges join-blocks candidates, validators enforce.

Reads the text task produced by `paperwright text-prepare`, extracts adjacent
body-block pairs that satisfy every hard join-blocks precondition (same page,
adjacent order, body kind, editable, unrelated, no sentence-terminal punctuation,
lowercase continuation), then asks a plain-text model to decide — for each pair —
whether it is a same-paragraph split. The model only decides; it never rewrites
text, and every join still passes `validate-text-review` before it is applied.

This is an optional tool: the `openai` package is only imported when the script
actually runs the model, so PaperWright's core stays free of LLM dependencies.

Token economics: each pair contributes only a tail/head snippet (semantic
filtering), and all pairs are batched into one call per page batch.

Usage:
    export DASHSCOPE_API_KEY=...
    PYTHONPATH=src python tools/run_text_review.py text-task.json text-review.json
    PYTHONPATH=src python tools/run_text_review.py text-task.json - --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from paperwright.text_review import text_task_sha256

MODEL = os.environ.get("PW_TEXT_MODEL", "qwen3.7-plus")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
REVIEWER = "paperwright-text-review-bridge"
TAIL = 70
HEAD = 70
BATCH = 25

# Same signal as paperwright.text_review._SENTENCE_TERMINAL, kept local so this
# tool does not depend on a private API.
_SENTENCE_TERMINAL = re.compile(r"[.!?:;]\s*$")


def _load_client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise SystemExit(
            "缺少可选依赖 openai：pip install openai（核心 paperwright 不需要）"
        ) from exc
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        key_file = Path.home() / ".dashscope_key"
        if key_file.is_file():
            key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("未设置 DASHSCOPE_API_KEY")
    return OpenAI(api_key=key, base_url=BASE_URL)


def extract_candidates(task: dict) -> list[tuple[dict, dict]]:
    blocks = task["blocks"]
    by_order = {b["order"]: b for b in blocks}
    candidates: list[tuple[dict, dict]] = []
    for b in blocks:
        nxt = by_order.get(b["order"] + 1)
        if nxt is None or b["page"] != nxt["page"]:
            continue
        if any(
            not blk["editable"] or blk["kind"] != "body" or blk["in_relations"]
            for blk in (b, nxt)
        ):
            continue
        curr_md = nxt["markdown"].lstrip().removeprefix("&emsp;")
        if _SENTENCE_TERMINAL.search(b["markdown"]):
            continue
        if curr_md[:1].islower():
            candidates.append((b, nxt))
    return candidates


def _snippet(text: str, tail: bool) -> str:
    text = text.replace("\n", " ").strip()
    return text[-TAIL:] if tail else text[:HEAD]


def build_prompt(candidates: list[tuple[dict, dict]]) -> str:
    lines = [
        f"[{i}] PREV ends: \"...{_snippet(p['markdown'], True)}\" | "
        f"NEXT starts: \"{_snippet(c['markdown'], False)}...\""
        for i, (p, c) in enumerate(candidates)
    ]
    return (
        "You are reviewing paragraph boundaries in a scientific paper's extracted text.\n"
        "Each candidate below is a pair of ADJACENT text blocks. The second block starts "
        "with a lowercase letter, which is a hint — but NOT proof — that it continues the "
        "first block.\n\n"
        "Decide for each pair whether block 2 is a DIRECT continuation of block 1 "
        "(SAME_PARAGRAPH) or a separate block (DIFFERENT_PARAGRAPHS).\n"
        "Traps to watch: author name/affiliation/email lines, a heading followed by body "
        "text, and reference entries are NOT the same paragraph even if they start lowercase.\n"
        "Only answer SAME_PARAGRAPH when the two halves are clearly one continuous sentence "
        "or phrase split by the extractor; when in doubt, answer DIFFERENT_PARAGRAPHS.\n\n"
        "Output ONLY a JSON object:\n"
        '{"decisions": [{"index": <int>, "verdict": "SAME_PARAGRAPH"|"DIFFERENT_PARAGRAPHS", '
        '"reason": "<one short sentence>"}]}\n\n'
        f"Candidates:\n" + "\n".join(lines)
    )


def judge(client, candidates: list[tuple[dict, dict]]) -> list[dict]:
    decisions: list[dict] = []
    for start in range(0, len(candidates), BATCH):
        batch = candidates[start : start + BATCH]
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": build_prompt(batch)}],
            temperature=0,
            max_tokens=2048,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        for item in data.get("decisions", []):
            item["_batch_offset"] = start
        decisions.extend(data.get("decisions", []))
    return decisions


def build_review(task: dict, candidates: list[tuple[dict, dict]], decisions: list[dict]) -> dict:
    verdict_by_key = {
        item.get("_batch_offset", 0) + int(item.get("index", -1)): item
        for item in decisions
    }
    operations = []
    for i, (prev, curr) in enumerate(candidates):
        item = verdict_by_key.get(i)
        if item is None or item.get("verdict") != "SAME_PARAGRAPH":
            continue
        reason = str(item.get("reason") or "Same paragraph split by the extractor.")
        operations.append(
            {
                "op": "join-blocks",
                "target_block_ids": [prev["id"], curr["id"]],
                "reason": reason[:1000],
            }
        )
    return {
        "contract_version": "paperwright-text-review-v0.2",
        "task_sha256": text_task_sha256(task),
        "source_sha256": task["source_sha256"],
        "article_model_sha256": task["article_model"]["sha256"],
        "reviewer": REVIEWER,
        "operations": operations,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_json", type=Path)
    ap.add_argument("review_json", type=Path)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--dry-run", action="store_true", help="只列候选对，不调模型")
    args = ap.parse_args()

    task = json.loads(args.task_json.read_text(encoding="utf-8"))
    candidates = extract_candidates(task)
    print(f"候选对: {len(candidates)}", file=sys.stderr)

    if args.dry_run:
        for i, (p, c) in enumerate(candidates):
            print(f"[{i}] {p['order']}->{c['order']} | ...{_snippet(p['markdown'], True)} | {_snippet(c['markdown'], False)}...")
        return 0

    client = _load_client()
    decisions = judge(client, candidates)
    review = build_review(task, candidates, decisions)
    if str(args.review_json) == "-":
        print(json.dumps(review, ensure_ascii=False, indent=1))
    else:
        args.review_json.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    n_join = len(review["operations"])
    print(f"判定: {n_join} 对拼接, {len(candidates) - n_join} 对不拼", file=sys.stderr)
    for op in review["operations"]:
        print(f"  join {op['target_block_ids'][0][:14]}.. -> {op['target_block_ids'][1][:14]}.. | {op['reason'][:60]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
