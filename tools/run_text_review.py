#!/usr/bin/env python3
"""Pure-text review bridge: model judges join-blocks candidates, validators enforce.

Reads the text task produced by `paperwright text-prepare`, extracts adjacent
body-block pairs that satisfy every hard join-blocks precondition (same page,
forward reading-order adjacency, body kind, editable, unrelated, no
sentence-terminal punctuation, lowercase continuation), then asks a plain-text
model to decide — for each pair — whether it is a same-paragraph split.

The model only decides; it never rewrites text.  The bridge validates every
review with `validate-text-review` before writing it, writes canonical JSON,
and refuses to overwrite an existing output file.

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
import sys
from pathlib import Path

from paperwright.exceptions import ContractValidationError
from paperwright.llm_cost import CostReport, canonical_cost_report_json
from paperwright.text_review import (
    canonical_text_review_json,
    join_candidates,
    text_task_sha256,
    validate_text_review,
)

MODEL = os.environ.get("PW_TEXT_MODEL", "qwen3.7-plus")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
REVIEWER = "paperwright-text-review-bridge"
TAIL = 70
HEAD = 70
BATCH = 25
VALID_VERDICTS = {"SAME_PARAGRAPH", "DIFFERENT_PARAGRAPHS"}


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
    # Shared with the L3 synthesis kernel: candidates are exactly the pairs
    # that validate-text-review can accept, so the two bridges cannot drift.
    return [(dict(previous), dict(current)) for previous, current in join_candidates(task)]


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


def validate_decisions(decisions: list[dict], candidate_count: int) -> list[dict]:
    """Reject malformed model output instead of silently skipping it.

    Every decision must have an integer batch offset and index that resolve to
    a unique candidate in range, and a verdict from the closed enum.
    """

    if not isinstance(decisions, list):
        raise ValueError("decisions 必须是数组")
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 0:
        raise ValueError("candidate_count 非法")
    seen: set[int] = set()
    normalized: list[dict] = []
    for item in decisions:
        if not isinstance(item, dict):
            raise ValueError("decision 必须是 JSON object")
        offset = item.get("_batch_offset", 0)
        index = item.get("index")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("decision _batch_offset 必须是非负整数")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("decision index 必须是整数")
        global_index = offset + index
        if global_index < 0 or global_index >= candidate_count:
            raise ValueError(
                f"decision index {global_index} 超出候选范围 0..{candidate_count - 1}"
            )
        if global_index in seen:
            raise ValueError(f"decision index {global_index} 重复")
        seen.add(global_index)
        verdict = item.get("verdict")
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                "decision verdict 必须是 SAME_PARAGRAPH 或 DIFFERENT_PARAGRAPHS"
            )
        reason = item.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ValueError("decision reason 必须是非空字符串")
        normalized.append(
            {
                "_batch_offset": offset,
                "index": index,
                "verdict": verdict,
                "reason": reason,
            }
        )
    return normalized


def judge(
    client,
    candidates: list[tuple[dict, dict]],
    *,
    model: str = MODEL,
) -> tuple[list[dict], CostReport]:
    decisions: list[dict] = []
    cost_report = CostReport()
    for start in range(0, len(candidates), BATCH):
        batch = candidates[start : start + BATCH]
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": build_prompt(batch)}],
            temperature=0,
            max_tokens=2048,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        cost_report.record(
            bridge=REVIEWER,
            model=model,
            step=f"batch-{start // BATCH + 1}",
            usage=getattr(resp, "usage", None),
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        for item in data.get("decisions", []):
            if not isinstance(item, dict):
                raise ValueError("decision 必须是 JSON object")
            item["_batch_offset"] = start
        decisions.extend(data.get("decisions", []))
    return decisions, cost_report


def build_review(
    task: dict,
    candidates: list[tuple[dict, dict]],
    decisions: list[dict],
    *,
    validate: bool = True,
) -> dict:
    decisions = validate_decisions(decisions, len(candidates))
    verdict_by_key = {
        item["_batch_offset"] + item["index"]: item for item in decisions
    }
    operations = []
    for i, (prev, curr) in enumerate(candidates):
        item = verdict_by_key.get(i)
        if item is None or item["verdict"] != "SAME_PARAGRAPH":
            continue
        reason = item.get("reason") or "Same paragraph split by the extractor."
        operations.append(
            {
                "op": "join-blocks",
                "target_block_ids": [prev["id"], curr["id"]],
                "reason": reason[:1000],
            }
        )
    review = {
        "contract_version": "paperwright-text-review-v0.2",
        "task_sha256": text_task_sha256(task),
        "source_sha256": task["source_sha256"],
        "article_model_sha256": task["article_model"]["sha256"],
        "reviewer": REVIEWER,
        "operations": operations,
    }
    if validate:
        validate_text_review(review, task=task)
    return review


def _write_review(review_path: Path, review: dict, task: dict) -> None:
    canonical = canonical_text_review_json(review, task=task)
    if str(review_path) == "-":
        sys.stdout.write(canonical)
        return
    review_path.write_text(canonical, encoding="utf-8", newline="\n")


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

    cost_path = None
    if str(args.review_json) != "-":
        cost_path = args.review_json.with_name(
            args.review_json.stem + ".usage.json"
        )
        if cost_path.exists():
            raise SystemExit(f"输出文件已存在，拒绝覆盖: {cost_path}")
    if str(args.review_json) != "-" and args.review_json.exists():
        raise SystemExit(f"输出文件已存在，拒绝覆盖: {args.review_json}")

    client = _load_client()
    try:
        decisions, cost_report = judge(client, candidates, model=args.model)
        review = build_review(task, candidates, decisions)
    except (ValueError, json.JSONDecodeError, ContractValidationError) as exc:
        raise SystemExit(f"L1 桥拒绝模型输出: {exc}") from exc

    _write_review(args.review_json, review, task)
    if cost_path is not None:
        cost_path.write_text(
            canonical_cost_report_json(cost_report),
            encoding="utf-8",
            newline="\n",
        )
    n_join = len(review["operations"])
    print(f"判定: {n_join} 对拼接, {len(candidates) - n_join} 对不拼", file=sys.stderr)
    totals = cost_report.totals()
    print(
        f"用量: {totals['call_count']} 次调用, "
        f"{totals['input_tokens']} in / {totals['output_tokens']} out tokens, "
        f"估算 ${totals['estimated_cost_usd_known']}",
        file=sys.stderr,
    )
    for op in review["operations"]:
        print(f"  join {op['target_block_ids'][0][:14]}.. -> {op['target_block_ids'][1][:14]}.. | {op['reason'][:60]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
