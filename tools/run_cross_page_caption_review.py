#!/usr/bin/env python3
"""Resolve adjacent-page Figure/Table caption relations with paired images."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from paperwright.cross_page_caption import (
    CROSS_PAGE_CAPTION_PROMPT_VERSION,
    CROSS_PAGE_CAPTION_REVIEW_FILENAME,
    CROSS_PAGE_CAPTION_REVIEW_VERSION,
    CROSS_PAGE_CAPTION_TASK_FILENAME,
    CROSS_PAGE_CAPTION_USAGE_FILENAME,
    build_cross_page_caption_task,
    canonical_cross_page_caption_review_json,
    canonical_cross_page_caption_task_json,
    cross_page_caption_task_sha256,
    empty_cross_page_caption_review,
    native_caption_text,
    validate_cross_page_caption_review,
)
from paperwright.exceptions import ContractValidationError
from paperwright.layout_models import FinalLayout, LayoutTask
from paperwright.layout_writer import materialize_layout_sources
from paperwright.models import PhysicalDocument


MODEL = os.environ.get("PW_VISUAL_MODEL", "qwen3.7-plus")
BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MAX_ATTEMPTS = 3


def _strip_fences(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _load_client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise SystemExit("缺少可选依赖 openai：pip install openai") from exc
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        key_path = Path.home() / ".dashscope_key"
        if key_path.is_file():
            key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("未设置 DASHSCOPE_API_KEY")
    return OpenAI(api_key=key, base_url=BASE_URL)


def _load_task(review_dir: Path) -> tuple[dict[str, Any], set[int]]:
    document = PhysicalDocument.from_dict(
        json.loads(
            (
                review_dir / "extraction-cache" / "physical-document.json"
            ).read_text(encoding="utf-8")
        )
    )
    layouts = []
    for page in document.pages:
        page_dir = review_dir / f"page-{page.page_index + 1:04d}"
        task = LayoutTask.from_dict(
            json.loads((page_dir / "layout-task.json").read_text(encoding="utf-8"))
        )
        layout = FinalLayout.from_dict(
            json.loads((page_dir / "final-layout.json").read_text(encoding="utf-8"))
        )
        layouts.append(materialize_layout_sources(layout, task, page))
    value = build_cross_page_caption_task(
        document,
        tuple(layouts),
        caption_text=native_caption_text,
    )
    pages = {
        page_index
        for pair in value["pairs"]
        for page_index in (
            pair["caption"]["page_index"],
            *(item["page_index"] for item in pair["visual_candidates"]),
        )
    }
    return value, pages


def _prompt(task: dict[str, Any]) -> str:
    compact = {
        "source_sha256": task["source_sha256"],
        "pairs": task["pairs"],
    }
    return f"""You review cross-page Figure/Table caption relations in a scientific paper.
The supplied images are complete adjacent PDF pages. Geometry and native caption text are
read-only evidence. For every caption_ref, either select exactly one listed visual_ref or
reject it. Do not transcribe, rewrite, summarize, invent text, draw boxes, or create refs.

Return JSON only:
{{"bindings":[{{"caption_ref":"p0002:r-caption","visual_ref":"p0001:r-figure","confidence":0.9}}],"rejected_caption_refs":[],"warnings":[]}}

Task:
{json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}
"""


def _image_content(path: Path) -> dict[str, Any]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


def _review(task: dict[str, Any], review_dir: Path, pages: set[int], model: str):
    client = _load_client()
    content: list[dict[str, Any]] = [{"type": "text", "text": _prompt(task)}]
    for page_index in sorted(pages):
        content.append(
            {
                "type": "text",
                "text": f"Page {page_index + 1}:",
            }
        )
        content.append(
            _image_content(
                review_dir / f"page-{page_index + 1:04d}" / "page.png"
            )
        )
    last_error: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0,
            )
            raw = json.loads(
                _strip_fences(response.choices[0].message.content or "")
            )
            value = {
                "contract_version": CROSS_PAGE_CAPTION_REVIEW_VERSION,
                "source_sha256": task["source_sha256"],
                "task_sha256": cross_page_caption_task_sha256(task),
                "reviewer": f"dashscope-openai/{model}",
                "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
                "bindings": raw.get("bindings", []),
                "rejected_caption_refs": raw.get(
                    "rejected_caption_refs", []
                ),
                "warnings": raw.get("warnings", []),
            }
            validate_cross_page_caption_review(value, task)
            usage = getattr(response, "usage", None)
            usage_value = {
                "contract_version": "paperwright-provider-usage-v0.1",
                "model": model,
                "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
                "call_count": 1,
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "reasoning_tokens": int(
                    getattr(
                        getattr(usage, "completion_tokens_details", None),
                        "reasoning_tokens",
                        0,
                    )
                    or 0
                ),
            }
            return value, usage_value
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ContractValidationError,
        ) as exc:
            last_error = exc
    raise SystemExit(f"跨页 caption 复核失败: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review_dir", type=Path)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    review_dir = args.review_dir.expanduser().resolve()
    task, pages = _load_task(review_dir)
    print(
        f"cross-page-caption pairs={len(task['pairs'])} "
        f"pages={','.join(str(item + 1) for item in sorted(pages)) or '-'}"
    )
    if args.dry_run:
        return 0
    task_path = review_dir / CROSS_PAGE_CAPTION_TASK_FILENAME
    review_path = review_dir / CROSS_PAGE_CAPTION_REVIEW_FILENAME
    usage_path = review_dir / CROSS_PAGE_CAPTION_USAGE_FILENAME
    if task_path.is_file() and review_path.is_file():
        recorded_task = json.loads(task_path.read_text(encoding="utf-8"))
        if recorded_task != task:
            raise SystemExit("已有跨页 caption task 与最终布局不一致")
        recorded_review = json.loads(review_path.read_text(encoding="utf-8"))
        validate_cross_page_caption_review(recorded_review, recorded_task)
        print("cross-page-caption existing review validated")
        return 0
    if task_path.exists() or review_path.exists() or usage_path.exists():
        raise SystemExit("跨页 caption task/review/usage 不完整，拒绝覆盖")
    if task["pairs"]:
        review, usage = _review(task, review_dir, pages, args.model)
    else:
        review = empty_cross_page_caption_review(task)
        usage = {
            "contract_version": "paperwright-provider-usage-v0.1",
            "model": None,
            "prompt_version": CROSS_PAGE_CAPTION_PROMPT_VERSION,
            "call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
        }
    task_path.write_text(
        canonical_cross_page_caption_task_json(task),
        encoding="utf-8",
        newline="\n",
    )
    review_path.write_text(
        canonical_cross_page_caption_review_json(review, task=task),
        encoding="utf-8",
        newline="\n",
    )
    usage_path.write_text(
        json.dumps(usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
