#!/usr/bin/env python3
"""L3 program-synthesis bridge (minimal prototype, join-blocks).

The model writes a RESTRICTED DSL script instead of emitting declarative JSON.
That script is validated against an AST whitelist, executed in a clean
namespace with a read-only ReviewAPI, and its `emit_join` calls become the same
join-blocks operations that L1 produces — so the exact same validate-text-review
chain applies. Word-bag conservation re-derives every merged text and asserts
no characters were added, dropped, or changed.

This is the L3 "long-tail last resort" layer from docs/VISION.md §8.3. It does
not bypass validators; it only changes how the judgment logic is produced
(one-shot inference -> reproducible code).

Usage:
    export DASHSCOPE_API_KEY=...
    PYTHONPATH=src python tools/run_text_synthesize.py \
        article-model.json text-task.json text-review.json [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from paperwright.text_review import _join_joiner, text_task_sha256

MODEL = os.environ.get("PW_SYNTH_MODEL", "qwen3.7-plus")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
REVIEWER = "paperwright-synthesize-bridge"
MAX_ITERATIONS = 10000  # range() 迭代上限，兜底防死循环
MAX_SCRIPT_TIME = 5.0  # 秒

_SENTENCE_TERMINAL = re.compile(r"[.!?:;]\s*$")
# 统一连字符变体（用于守恒校验的字符规范化）
_HYPHEN_VARIANTS = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}


# ──────────────────────────────────────────────────────────────────────────
# 数据准备：把 article-model 的 bbox 合并进 text-task 的块
# ──────────────────────────────────────────────────────────────────────────

def _enrich_blocks(task: dict, article_model: dict) -> list[dict]:
    bbox_by_id: dict[str, dict] = {}
    for blk in article_model["blocks"]:
        spans = blk.get("source_spans") or []
        if spans and isinstance(spans[0].get("bbox"), dict):
            bbox_by_id[blk["id"]] = spans[0]["bbox"]
    enriched = []
    for b in task["blocks"]:
        nb = dict(b)
        nb["bbox"] = bbox_by_id.get(b["id"])
        enriched.append(nb)
    return enriched


# ──────────────────────────────────────────────────────────────────────────
# ReviewAPI：只读查询 + 结构化 emit
# ──────────────────────────────────────────────────────────────────────────

class Block(dict):
    """只读块视图：同时支持 a["markdown"] 与 a.markdown 两种访问。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ReviewAPI:
    def __init__(self, blocks: list[dict]):
        self._blocks = [Block(b) for b in blocks]
        self.emitted: list[dict] = []

    def blocks(self) -> list[Block]:
        return [Block(b) for b in self._blocks]

    def body_blocks(self) -> list[Block]:
        return [Block(b) for b in self._blocks if b["kind"] == "body"]

    def adjacent_body_pairs(self) -> list[tuple[Block, Block]]:
        """候选对：满足 join-blocks 全部硬性必要条件（复用校验器规则）。"""
        by_order = {b["order"]: b for b in self._blocks}
        pairs = []
        for b in self._blocks:
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
                pairs.append((b, nxt))
        return pairs

    # ── 几何原语（归一化坐标，y 向下）──

    def same_column(self, a: dict, b: dict) -> bool:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not ba or not bb:
            return False
        return not (ba["x"] + ba["width"] <= bb["x"] or bb["x"] + bb["width"] <= ba["x"])

    def vertical_gap(self, a: dict, b: dict) -> float | None:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not ba or not bb:
            return None
        return bb["y"] - (ba["y"] + ba["height"])

    def first_line_indent(self, block: dict) -> float | None:
        bbox = block.get("bbox")
        return bbox["x"] if bbox else None

    # ── 文本 ──

    def word_bag(self, text: str) -> dict:
        normalized = _normalize(text)
        return dict(Counter(normalized.split()))

    # ── 产出（声明意图，不执行拼接）──

    def emit_join(self, prev_id: str, curr_id: str, reason: str) -> None:
        self.emitted.append(
            {
                "op": "join-blocks",
                "target_block_ids": [prev_id, curr_id],
                "reason": str(reason)[:1000],
            }
        )


def _normalize(text: str) -> str:
    for h in _HYPHEN_VARIANTS:
        text = text.replace(h, "-")
    return " ".join(text.split())


# ──────────────────────────────────────────────────────────────────────────
# 守恒校验：emit 的 join 由校验器重算 merged，断言字符多重集无增删改
# ──────────────────────────────────────────────────────────────────────────

def _conservation_check(task: dict, operations: list[dict]) -> None:
    markdown_by_id = {b["id"]: b["markdown"] for b in task["blocks"]}
    for op in operations:
        if op["op"] != "join-blocks":
            continue
        prev_id, curr_id = op["target_block_ids"]
        prev = markdown_by_id[prev_id]
        curr = markdown_by_id[curr_id]
        merged = prev + _join_joiner(prev) + curr.lstrip()
        left = sorted(_normalize(merged).replace(" ", ""))
        right = sorted(_normalize(prev + curr).replace(" ", ""))
        if left != right:
            raise ValueError(
                f"守恒校验失败: join {prev_id[:14]}.. + {curr_id[:14]}.. 改变了字符集"
            )


# ──────────────────────────────────────────────────────────────────────────
# 受限 DSL：ast 白名单
# ──────────────────────────────────────────────────────────────────────────

_ALLOWED_NODES = {
    ast.Module, ast.Expr, ast.Assign, ast.AnnAssign,
    ast.For, ast.If, ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp,
    ast.Name, ast.Constant, ast.List, ast.Dict, ast.Tuple, ast.Subscript,
    ast.Attribute, ast.Call, ast.Load, ast.Store,
    ast.And, ast.Or, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.FloorDiv, ast.Pow, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn, ast.Not, ast.USub, ast.UAdd,
    ast.Break, ast.Continue, ast.keyword, ast.arguments, ast.comprehension,
    ast.ListComp, ast.GeneratorExp, ast.DictComp, ast.SetComp, ast.Starred,
    ast.Slice, ast.IfExp,
}

_FORBIDDEN_NODES = {
    ast.Import, ast.ImportFrom, ast.ClassDef, ast.Lambda, ast.FunctionDef,
    ast.AsyncFunctionDef, ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom,
    ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.ExceptHandler, ast.While,
    ast.Await, ast.AsyncFor, ast.Return, ast.Delete, ast.NamedExpr, ast.Match,
}

_ALLOWED_BUILTINS = {
    "len", "range", "sorted", "min", "max", "abs", "round",
    "sum", "any", "all", "enumerate", "zip", "int", "float", "str",
    "bool", "list", "dict", "tuple", "set", "True", "False", "None",
    "hasattr", "isinstance", "type",
}


class _DSLValidator(ast.NodeVisitor):
    def generic_visit(self, node):
        if type(node) in _FORBIDDEN_NODES:
            raise SyntaxError(f"forbidden syntax: {type(node).__name__}")
        if type(node) not in _ALLOWED_NODES:
            raise SyntaxError(f"forbidden node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__"):
            raise SyntaxError("forbidden dunder attribute access")
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name) and func.id not in _ALLOWED_BUILTINS:
            raise SyntaxError(f"forbidden function call: {func.id}")
        self.generic_visit(node)


class _LimitedRange:
    """range 的上限版本：防 for range(10**12) 这种失控迭代。"""

    def __call__(self, *args):
        r = range(*args)
        if len(r) > MAX_ITERATIONS:
            raise RuntimeError(f"range 迭代超过上限 {MAX_ITERATIONS}")
        return r


def _execute_dsl(code: str, api: ReviewAPI) -> list[dict]:
    import builtins
    import signal

    tree = ast.parse(code, mode="exec")
    _DSLValidator().visit(tree)
    namespace: dict = {"api": api}
    for name in _ALLOWED_BUILTINS:
        if name == "range":
            namespace[name] = _LimitedRange()
        elif name in ("True", "False", "None"):
            continue
        else:
            namespace[name] = getattr(builtins, name)
    compiled = compile(tree, "<synthesized>", "exec")

    def _timeout(_sig, _frame):
        raise TimeoutError("synthesized script exceeded time limit")

    old = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, MAX_SCRIPT_TIME)
    try:
        exec(compiled, namespace, namespace)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    return api.emitted


# ──────────────────────────────────────────────────────────────────────────
# 代码生成
# ──────────────────────────────────────────────────────────────────────────

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
        "NO try/except, NO dunder attributes.\n"
        "- Only call api.* methods and builtins: len, range, sorted, min, max, abs, round, "
        "sum, any, all, enumerate, zip, int, float, str, bool, list, dict, tuple, set.\n\n"
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


def _synthesize(client, api: ReviewAPI, task: dict, attempts: int = 3) -> tuple[str, list[dict]]:
    code = _generate_script(client, api)
    for _ in range(attempts):
        print(f"=== 尝试生成的 DSL 脚本 ===\n{code}\n=== 结束 ===", file=sys.stderr)
        try:
            operations = _execute_dsl(code, api)
            _conservation_check(task, operations)
            return code, operations
        except (SyntaxError, AttributeError, NameError, ValueError, TimeoutError, RuntimeError) as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            code = _repair_script(client, api, code, str(exc))
    raise SystemExit(f"L3 代码生成在 {attempts} 轮内未能产出可执行且守恒的脚本")


# ──────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────

def build_review(task: dict, operations: list[dict]) -> dict:
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
    ap.add_argument("article_model_json", type=Path)
    ap.add_argument("task_json", type=Path)
    ap.add_argument("review_json", type=Path)
    ap.add_argument("--script", type=Path, help="跳过模型，直接执行已有 DSL 脚本")
    ap.add_argument("--dry-run", action="store_true", help="只生成脚本不执行")
    args = ap.parse_args()

    article_model = json.loads(args.article_model_json.read_text(encoding="utf-8"))
    task = json.loads(args.task_json.read_text(encoding="utf-8"))
    blocks = _enrich_blocks(task, article_model)
    api = ReviewAPI(blocks)

    if args.script:
        code = args.script.read_text(encoding="utf-8")
        if args.dry_run:
            print(code)
            return 0
        operations = _execute_dsl(code, api)
        _conservation_check(task, operations)
    else:
        client = _load_client()
        if args.dry_run:
            print(_generate_script(client, api))
            return 0
        code, operations = _synthesize(client, api, task)

    print(f"DSL 产出 {len(operations)} 个 join", file=sys.stderr)
    review = build_review(task, operations)
    args.review_json.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"守恒校验通过，已写 {args.review_json.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
