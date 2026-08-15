"""Deterministic accounting for optional LLM bridge usage.

PaperWright's core never calls a model.  The optional bridges in ``tools/``
collect OpenAI-compatible usage objects through this module so every model
call can be measured, priced, and eventually budgeted without introducing
LLM dependencies into the package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

COST_CONTRACT_VERSION = "paperwright-llm-cost-v0.1"

# USD per million tokens.  Prices are editable configuration, not authoritative
# vendor pricing; unknown models fall back to these defaults with a note.
DEFAULT_INPUT_USD_PER_MTOK = 2.0
DEFAULT_OUTPUT_USD_PER_MTOK = 8.0
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "qwen3.7-plus": (2.0, 8.0),
    "qwen3.5-omni-plus": (3.0, 12.0),
    "qwen3.5-omni-flash": (1.0, 4.0),
}

_USAGE_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens")


def _tokens(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def usage_tokens(usage: object | None) -> tuple[int, int, int]:
    """Return ``(input, output, reasoning)`` from an OpenAI usage object."""

    if usage is None:
        return 0, 0, 0
    if isinstance(usage, Mapping):
        input_tokens = _tokens(usage.get("prompt_tokens"))
        output_tokens = _tokens(usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details")
        reasoning = (
            _tokens(details.get("reasoning_tokens"))
            if isinstance(details, Mapping)
            else 0
        )
        return input_tokens, output_tokens, reasoning
    input_tokens = _tokens(getattr(usage, "prompt_tokens", 0))
    output_tokens = _tokens(getattr(usage, "completion_tokens", 0))
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = _tokens(getattr(details, "reasoning_tokens", 0))
    return input_tokens, output_tokens, reasoning


@dataclass(frozen=True)
class ModelCallRecord:
    bridge: str
    model: str
    step: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    estimated_cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge": self.bridge,
            "model": self.model,
            "step": self.step,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> float | None:
    price = MODEL_PRICING.get(model)
    if price is None:
        return None
    input_usd, output_usd = price
    total = (
        (input_tokens + reasoning_tokens) * input_usd
        + output_tokens * output_usd
    ) / 1_000_000
    return round(total, 8)


def record_call(
    *,
    bridge: str,
    model: str,
    step: str,
    usage: object | None,
) -> ModelCallRecord:
    input_tokens, output_tokens, reasoning_tokens = usage_tokens(usage)
    return ModelCallRecord(
        bridge=bridge,
        model=model,
        step=step,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        estimated_cost_usd=estimate_cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        ),
    )


@dataclass
class CostReport:
    contract_version: str = COST_CONTRACT_VERSION
    records: list[ModelCallRecord] = field(default_factory=list)

    def add(self, record: ModelCallRecord) -> None:
        self.records.append(record)

    def record(
        self,
        *,
        bridge: str,
        model: str,
        step: str,
        usage: object | None,
    ) -> ModelCallRecord:
        record = record_call(
            bridge=bridge, model=model, step=step, usage=usage
        )
        self.records.append(record)
        return record

    def totals(self) -> dict[str, Any]:
        input_tokens = sum(item.input_tokens for item in self.records)
        output_tokens = sum(item.output_tokens for item in self.records)
        reasoning_tokens = sum(item.reasoning_tokens for item in self.records)
        known_cost = sum(
            item.estimated_cost_usd
            for item in self.records
            if item.estimated_cost_usd is not None
        )
        unknown_cost_calls = sum(
            item.estimated_cost_usd is None for item in self.records
        )
        return {
            "call_count": len(self.records),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "estimated_cost_usd_known": round(known_cost, 8),
            "unknown_pricing_call_count": unknown_cost_calls,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "totals": self.totals(),
            "records": [item.to_dict() for item in self.records],
        }


def canonical_cost_report_json(report: CostReport) -> str:
    return (
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


__all__ = [
    "COST_CONTRACT_VERSION",
    "CostReport",
    "ModelCallRecord",
    "canonical_cost_report_json",
    "estimate_cost_usd",
    "record_call",
    "usage_tokens",
]
