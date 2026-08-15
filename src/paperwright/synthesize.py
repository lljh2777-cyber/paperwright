"""Deterministic L3 program-synthesis layer for source-preserving text review.

The optional L3 bridge lets a model write a restricted DSL script instead of
emitting declarative ``text-review.json`` directly.  The script is validated
against an AST whitelist, executed against a read-only :class:`ReviewAPI`, and
its ``emit_join`` calls become the same ``join-blocks`` operations the L1
protocol produces — so the exact same ``validate-text-review`` chain applies.
Word-bag conservation re-derives every merged text and proves that no
characters were added, dropped, or changed.

This module contains only the deterministic kernel (DSL validation, execution,
conservation and review construction).  Model calls, prompt building and
self-repair live in ``tools/run_text_synthesize.py`` so PaperWright's core
keeps its no-network, no-LLM boundary.

See docs/VISION.md §8.3 for the product decision this implements.
"""

from __future__ import annotations

import ast
import builtins
from collections import Counter
from collections.abc import Mapping, Sequence as SequenceABC
from copy import deepcopy
import signal
import sys
import threading
from typing import Any
import unicodedata

from .exceptions import PaperWrightError
from .text_review import (
    TEXT_REVIEW_CONTRACT_VERSION,
    join_candidate_pairs,
    join_joiner,
    text_task_sha256,
    validate_text_review,
    validate_text_task,
)

SYNTHESIS_REVIEWER = "paperwright-synthesize-bridge"
MAX_ITERATIONS = 10000  # range() 迭代上限，兜底防死循环
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 5.0
DEFAULT_TICK_LIMIT = 500_000
MAX_SOURCE_CHARS = 200_000
MAX_STRING_LITERAL_CHARS = 10_000
MAX_INTEGER_LITERAL = 10**9

# 统一连字符变体（用于守恒校验的字符规范化）
_HYPHEN_VARIANTS = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}


class SynthesisError(PaperWrightError):
    """Expected failure inside the L3 synthesis kernel."""


class DSLValidationError(SynthesisError):
    """The synthesized script is outside the restricted DSL."""


class ConservationError(SynthesisError):
    """A declared join would change the source text character multiset."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for hyphen in _HYPHEN_VARIANTS:
        text = text.replace(hyphen, "-")
    return " ".join(text.split())


def _normalized_chars(text: str) -> list[str]:
    return sorted(_normalize(text).replace(" ", ""))


# ──────────────────────────────────────────────────────────────────────────
# 受限 DSL：ast 白名单
# ──────────────────────────────────────────────────────────────────────────

_ALLOWED_NODES = {
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AnnAssign,
    ast.For,
    ast.If,
    ast.Compare,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Constant,
    ast.List,
    ast.Dict,
    ast.Tuple,
    ast.Subscript,
    ast.Attribute,
    ast.Call,
    ast.Load,
    ast.Store,
    ast.And,
    ast.Or,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Break,
    ast.Continue,
    ast.keyword,
    ast.arguments,
    ast.comprehension,
    ast.ListComp,
    ast.GeneratorExp,
    ast.DictComp,
    ast.SetComp,
    ast.Starred,
    ast.Slice,
    ast.IfExp,
}

# Pow is deliberately absent: exponentiation is never needed for join decisions
# and would make giant-integer denial of service a one-line script.
_FORBIDDEN_NODES = {
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.Lambda,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Global,
    ast.Nonlocal,
    ast.Yield,
    ast.YieldFrom,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.ExceptHandler,
    ast.While,
    ast.Await,
    ast.AsyncFor,
    ast.Return,
    ast.Delete,
    ast.NamedExpr,
    ast.Match,
}

# str.format/format_map can read private attributes via format fields and are
# not needed for join decisions; keep reflection out of the DSL.
_FORBIDDEN_ATTRIBUTE_NAMES = {"format", "format_map"}

_ALLOWED_BUILTINS = {
    "len",
    "range",
    "sorted",
    "min",
    "max",
    "abs",
    "round",
    "sum",
    "any",
    "all",
    "enumerate",
    "zip",
    "int",
    "float",
    "str",
    "bool",
    "list",
    "dict",
    "tuple",
    "set",
    "True",
    "False",
    "None",
}

# VISION §8.3 决策 3 明确禁反射/IO：这些名字既不进入命名空间，也被静态拒绝，
# 双保险避免 exec 默认 __builtins__ 把能力漏进沙箱。
_FORBIDDEN_BUILTIN_NAMES = {
    "__import__",
    "breakpoint",
    "classmethod",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "exit",
    "getattr",
    "globals",
    "hasattr",
    "help",
    "input",
    "isinstance",
    "locals",
    "memoryview",
    "object",
    "open",
    "print",
    "property",
    "quit",
    "setattr",
    "staticmethod",
    "super",
    "type",
    "vars",
}


class _DSLValidator(ast.NodeVisitor):
    def generic_visit(self, node):
        if type(node) in _FORBIDDEN_NODES:
            raise SyntaxError(f"forbidden syntax: {type(node).__name__}")
        if type(node) not in _ALLOWED_NODES:
            raise SyntaxError(f"forbidden node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node):
        if node.id in _FORBIDDEN_BUILTIN_NAMES:
            raise SyntaxError(f"forbidden builtin name: {node.id}")
        self.generic_visit(node)

    def visit_Constant(self, node):
        value = node.value
        if isinstance(value, bytes):
            raise SyntaxError("forbidden bytes literal")
        if isinstance(value, complex):
            raise SyntaxError("forbidden complex literal")
        if isinstance(value, int) and not isinstance(value, bool):
            if abs(value) > MAX_INTEGER_LITERAL:
                raise SyntaxError("integer literal is too large")
        if isinstance(value, str) and len(value) > MAX_STRING_LITERAL_CHARS:
            raise SyntaxError("string literal is too long")
        if value is not Ellipsis:
            self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("_"):
            raise SyntaxError("forbidden private attribute access")
        if node.attr in _FORBIDDEN_ATTRIBUTE_NAMES:
            raise SyntaxError(f"forbidden attribute: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in _ALLOWED_BUILTINS:
                raise SyntaxError(f"forbidden function call: {func.id}")
        elif not isinstance(func, ast.Attribute):
            raise SyntaxError("forbidden call target")
        self.generic_visit(node)


class _LimitedRange:
    """range 的上限版本：防 for range(10**12) 这种失控迭代。"""

    def __init__(self, max_iterations: int):
        self.max_iterations = max_iterations

    def __call__(self, *args):
        try:
            value = range(*args)
            if len(value) > self.max_iterations:
                raise RuntimeError(
                    f"range 迭代超过上限 {self.max_iterations}"
                )
            return value
        except (OverflowError, TypeError, ValueError) as exc:
            raise RuntimeError(f"range 参数非法: {exc}") from exc


def _validate_dsl_code(code: str) -> ast.Module:
    if not isinstance(code, str):
        raise DSLValidationError("DSL script 必须是字符串")
    if len(code) > MAX_SOURCE_CHARS:
        raise DSLValidationError(f"DSL script 超过 {MAX_SOURCE_CHARS} 字符上限")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise DSLValidationError(f"DSL 语法错误: {exc}") from exc
    try:
        _DSLValidator().visit(tree)
    except SyntaxError as exc:
        raise DSLValidationError(str(exc)) from exc
    return tree


def _dsl_namespace(api: "ReviewAPI", *, max_iterations: int) -> dict[str, Any]:
    namespace: dict[str, Any] = {"api": api, "__builtins__": {}}
    for name in sorted(_ALLOWED_BUILTINS):
        if name in ("True", "False", "None"):
            continue
        if name == "range":
            namespace[name] = _LimitedRange(max_iterations)
        else:
            namespace[name] = getattr(builtins, name)
    return namespace


def _execute_bounded(
    compiled: object,
    namespace: dict[str, Any],
    *,
    timeout_seconds: float,
    tick_limit: int,
) -> None:
    """Execute compiled DSL code under a cross-platform instruction budget and
    a best-effort POSIX wall-clock timeout."""

    ticks = 0

    def _trace(_frame, _event, _arg):
        nonlocal ticks
        ticks += 1
        if ticks > tick_limit:
            raise TimeoutError(
                f"synthesized script exceeded instruction limit {tick_limit}"
            )
        return _trace

    old_trace = sys.gettrace()
    sys.settrace(_trace)
    old_handler = None
    timer_installed = False
    try:
        if (
            timeout_seconds is not None
            and timeout_seconds > 0
            and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        ):
            def _timeout(_sig, _frame):
                raise TimeoutError(
                    "synthesized script exceeded time limit"
                )

            old_handler = signal.signal(signal.SIGALRM, _timeout)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            timer_installed = True
        exec(compiled, namespace, namespace)
    finally:
        if timer_installed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        sys.settrace(old_trace)


def execute_dsl(
    code: str,
    api: "ReviewAPI",
    *,
    max_iterations: int = MAX_ITERATIONS,
    timeout_seconds: float = DEFAULT_SCRIPT_TIMEOUT_SECONDS,
    tick_limit: int = DEFAULT_TICK_LIMIT,
) -> list[dict[str, Any]]:
    """Validate and run a restricted DSL script against a read-only ReviewAPI.

    Returns a deep copy of the declared operations.  It does not write
    ``text-review.json``; combine the result with :func:`build_synthesis_review`
    so the regular L1 validator stays the source of truth.
    """

    if not isinstance(api, ReviewAPI):
        raise SynthesisError("execute_dsl 只接受 ReviewAPI")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise SynthesisError("max_iterations 必须是整数")
    if max_iterations < 1:
        raise SynthesisError("max_iterations 必须为正")
    if isinstance(tick_limit, bool) or not isinstance(tick_limit, int):
        raise SynthesisError("tick_limit 必须是整数")
    if tick_limit < 1:
        raise SynthesisError("tick_limit 必须为正")

    tree = _validate_dsl_code(code)
    namespace = _dsl_namespace(api, max_iterations=max_iterations)
    compiled = compile(tree, "<synthesized>", "exec")
    _execute_bounded(
        compiled,
        namespace,
        timeout_seconds=timeout_seconds,
        tick_limit=tick_limit,
    )
    return api.operations()


# ──────────────────────────────────────────────────────────────────────────
# ReviewAPI：只读查询 + 结构化 emit
# ──────────────────────────────────────────────────────────────────────────

class Block(dict):
    """只读块视图：同时支持 ``a["markdown"]`` 与 ``a.markdown`` 两种访问。"""

    def __init__(self, value: Mapping[str, Any]):
        super().__init__({key: item for key, item in value.items()})

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @staticmethod
    def _readonly(*_args, **_kwargs):
        raise TypeError("text blocks are read-only inside the synthesis DSL")

    __setitem__ = _readonly
    __delitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    update = _readonly

    def copy(self):
        return dict(self)


class ReviewAPI:
    """Read-only query surface plus structured ``emit_join`` declarations."""

    def __init__(
        self,
        blocks: SequenceABC[Mapping[str, Any]],
        *,
        join_allowed: bool = True,
    ):
        self._blocks = [Block(block) for block in blocks]
        self._block_by_id = {block["id"]: block for block in self._blocks}
        self._join_allowed = bool(join_allowed)
        self._emitted: list[dict[str, Any]] = []

    @property
    def emitted(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(operation) for operation in self._emitted)

    def operations(self) -> list[dict[str, Any]]:
        return deepcopy(self._emitted)

    def blocks(self) -> list[Block]:
        return [Block(block) for block in self._blocks]

    def body_blocks(self) -> list[Block]:
        return [Block(block) for block in self._blocks if block["kind"] == "body"]

    def adjacent_body_pairs(self) -> list[tuple[Block, Block]]:
        """候选对：满足 join-blocks 全部硬性必要条件（复用校验器规则）。"""
        if not self._join_allowed:
            return []
        return [
            (Block(previous), Block(current))
            for previous, current in join_candidate_pairs(self._blocks)
        ]

    # ── 几何原语（坐标单位沿用 task/article-model 的声明，y 向下）──

    def same_column(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not ba or not bb:
            return False
        return not (
            ba["x"] + ba["width"] <= bb["x"]
            or bb["x"] + bb["width"] <= ba["x"]
        )

    def vertical_gap(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
        ba, bb = a.get("bbox"), b.get("bbox")
        if not ba or not bb:
            return None
        return bb["y"] - (ba["y"] + ba["height"])

    def first_line_indent(self, block: Mapping[str, Any]) -> float | None:
        bbox = block.get("bbox")
        return bbox["x"] if bbox else None

    # ── 文本 ──

    def word_bag(self, text: str) -> dict[str, int]:
        normalized = _normalize(text)
        return dict(Counter(normalized.split()))

    # ── 产出（声明意图，不执行拼接）──

    def emit_join(self, prev_id: str, curr_id: str, reason: str) -> None:
        if not self._join_allowed:
            raise ValueError("emit_join 不在 text task 允许的操作中")
        if not isinstance(prev_id, str) or not isinstance(curr_id, str):
            raise TypeError("emit_join block ids 必须是字符串")
        if prev_id == curr_id:
            raise ValueError("emit_join 不能拼接同一个块")
        if prev_id not in self._block_by_id or curr_id not in self._block_by_id:
            raise ValueError("emit_join block id 不存在")
        used = {
            block_id
            for operation in self._emitted
            for block_id in operation["target_block_ids"]
        }
        if prev_id in used or curr_id in used:
            raise ValueError("emit_join block 不能出现在多个操作中")
        if not isinstance(reason, str):
            raise TypeError("emit_join reason 必须是字符串")
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("emit_join reason 必须是非空且不超过 1000 字符")
        self._emitted.append(
            {
                "op": "join-blocks",
                "target_block_ids": [prev_id, curr_id],
                "reason": reason,
            }
        )


# ──────────────────────────────────────────────────────────────────────────
# 数据准备：把 article-model 的 bbox 合并进 text-task 的块
# ──────────────────────────────────────────────────────────────────────────

def enrich_task_blocks(
    task: Mapping[str, Any],
    article_model: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return task blocks with a ``bbox`` field from the bound article model.

    Both inputs are validated first, so a stale or mismatched model cannot
    silently feed wrong geometry into a synthesized script.
    """

    validate_text_task(task, article_model=article_model)
    bbox_by_id: dict[str, Mapping[str, Any]] = {}
    for block in article_model["blocks"]:
        spans = block.get("source_spans") or []
        if spans and isinstance(spans[0], Mapping):
            bbox = spans[0].get("bbox")
            if isinstance(bbox, Mapping):
                bbox_by_id[block["id"]] = dict(bbox)
    return [
        {**block, "bbox": bbox_by_id.get(block["id"])}
        for block in task["blocks"]
    ]


# ──────────────────────────────────────────────────────────────────────────
# 守恒校验：emit 的 join 由校验器重算 merged，断言字符多重集无增删改
# ──────────────────────────────────────────────────────────────────────────

def verify_join_conservation(
    task: Mapping[str, Any],
    operations: SequenceABC[Mapping[str, Any]],
) -> None:
    """Re-derive every declared join and prove the character multiset is kept.

    The validator proves the structure is legal; this check proves the merge
    is text-conserving even if the executor carried a bug.
    """

    validate_text_task(task)
    if not isinstance(operations, SequenceABC) or isinstance(operations, (str, bytes)):
        raise ConservationError("synthesis operations 必须是数组")
    markdown_by_id = {block["id"]: block["markdown"] for block in task["blocks"]}
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ConservationError("synthesis operation 必须是对象")
        if operation.get("op") != "join-blocks":
            raise ConservationError(
                "synthesis DSL 只能产出 join-blocks 操作"
            )
        target = operation.get("target_block_ids")
        if not isinstance(target, list) or len(target) != 2:
            raise ConservationError("join-blocks target_block_ids 必须恰好两个")
        prev_id, curr_id = target
        if not isinstance(prev_id, str) or not isinstance(curr_id, str):
            raise ConservationError("join-blocks target_block_ids 非法")
        previous = markdown_by_id.get(prev_id)
        current = markdown_by_id.get(curr_id)
        if previous is None or current is None:
            raise ConservationError("join-blocks block 不存在")
        merged = previous + join_joiner(previous) + current.lstrip()
        if _normalized_chars(merged) != _normalized_chars(previous + current):
            raise ConservationError(
                f"守恒校验失败: join {prev_id[:14]}.. + {curr_id[:14]}.. "
                "改变了字符集"
            )


def build_synthesis_review(
    task: Mapping[str, Any],
    operations: SequenceABC[Mapping[str, Any]],
    *,
    reviewer: str = SYNTHESIS_REVIEWER,
) -> dict[str, Any]:
    """Build an L1-compatible text-review.json and validate it before return."""

    validate_text_task(task)
    verify_join_conservation(task, operations)
    review = {
        "contract_version": TEXT_REVIEW_CONTRACT_VERSION,
        "task_sha256": text_task_sha256(task),
        "source_sha256": task["source_sha256"],
        "article_model_sha256": task["article_model"]["sha256"],
        "reviewer": reviewer,
        "operations": list(operations),
    }
    validate_text_review(review, task=task)
    return review


__all__ = [
    "Block",
    "ConservationError",
    "DSLValidationError",
    "DEFAULT_SCRIPT_TIMEOUT_SECONDS",
    "DEFAULT_TICK_LIMIT",
    "MAX_ITERATIONS",
    "ReviewAPI",
    "SynthesisError",
    "SYNTHESIS_REVIEWER",
    "build_synthesis_review",
    "enrich_task_blocks",
    "execute_dsl",
    "verify_join_conservation",
]
