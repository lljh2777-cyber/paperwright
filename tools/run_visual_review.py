#!/usr/bin/env python3
"""Direct DashScope visual-layout review bridge for PaperWright.

This is the official fallback for the qwen-mm-plugins MCP path.  It reads a
review bundle prepared by `paperwright layout-prepare` (visual-direct mode),
asks a multimodal model directly over the OpenAI-compatible DashScope endpoint
to draw final semantic regions from each `page.png`, and writes
`final-layout.json` per page.  Every produced layout is validated by
`paperwright validate-final-layout` before it is persisted.

The model only decides geometry and roles; PaperWright still assigns native
PDF elements and validators remain the source of truth.

Usage:
    export DASHSCOPE_API_KEY=...
    PYTHONPATH=src python tools/run_visual_review.py layout-review/
    PYTHONPATH=src python tools/run_visual_review.py layout-review/ --pages 1-3
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

from paperwright.exceptions import ContractValidationError
from paperwright.layout_models import FinalLayout, LayoutTask
from paperwright.llm_cost import CostReport, canonical_cost_report_json
from paperwright.layout_review import validate_layout_review

MODEL = os.environ.get("PW_VISUAL_MODEL", "qwen3.7-plus")
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT_VERSION = "paperwright-layout-review-prompt-v0.4"
MAX_ATTEMPTS = 3
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _load_config() -> tuple[str, str]:
    """Resolve API key/base URL from env or qwen-mm-plugins config files."""

    key = os.environ.get("DASHSCOPE_API_KEY")
    base_url = os.environ.get("DASHSCOPE_BASE_URL")
    if not key:
        for config_path in (
            Path.home() / ".qwen-mm-plugins" / "config",
            Path.home() / ".dashscope_key",
        ):
            if config_path.is_file():
                for raw in config_path.read_text(encoding="utf-8").splitlines():
                    raw = raw.strip()
                    if raw.startswith("DASHSCOPE_API_KEY="):
                        key = raw.split("=", 1)[1].strip().strip('"').strip("'")
                    elif raw.startswith("DASHSCOPE_BASE_URL="):
                        base_url = raw.split("=", 1)[1].strip().strip('"').strip("'")
                    elif "=" not in raw and not raw.startswith("#"):
                        key = raw
                if key:
                    break
    if not key:
        raise SystemExit("未找到 DASHSCOPE_API_KEY（请导出环境变量或配置 ~/.qwen-mm-plugins/config）")
    return key, base_url or DEFAULT_BASE_URL


def _load_client():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("缺少可选依赖 openai：pip install openai") from exc
    key, base_url = _load_config()
    return OpenAI(api_key=key, base_url=base_url), base_url


def _parse_pages(pages_arg: str | None) -> set[int] | None:
    if not pages_arg:
        return None
    pages: set[int] = set()
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))
    return pages


def _data_url(path: Path) -> str:
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise SystemExit(f"图片过大，拒绝上传: {path}")
    mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _prompt(task: LayoutTask) -> str:
    roi = task.metadata.get("analysis_roi")
    return f"""You are reviewing ONE page image of a scientific paper for PaperWright's hybrid layout pipeline.

Page index: {task.page.page_index}
Page size: {task.page.width:.1f} x {task.page.height:.1f} PDF points.
Coordinates: normalized to the full page, top-left origin, y down, values 0-1.
Confirmed Content ROI (all non-exclude regions must stay inside):
{json.dumps(roi, ensure_ascii=False)}

Return ONLY a JSON object (no markdown fences, no prose) in exactly this shape:
{{
  "regions": [
    {{
      "region_id": "r1",
      "bbox": {{"x": 0.05, "y": 0.05, "width": 0.4, "height": 0.1}},
      "content_class": "text|visual|exclude|unknown",
      "role": "body|heading|figure|table|caption|footnote|header|footer|margin|equation|other|unknown",
      "order": 1,
      "parent_region_id": null,
      "confidence": 0.9
    }}
  ]
}}

Rules:
- Draw every logical block: headings, body paragraphs/columns, figures/tables
  (one visual region per figure/table containing all panels), captions
  separately, footnotes.
- Page furniture (running header/footer, page number, journal banner) must be
  content_class "exclude" with role header/footer/margin and NO order.
- Non-exclude regions need consecutive reading order 1,2,3...
- Caption regions: role "caption", content_class "text"; set parent_region_id
  to the visual region id when the figure/table is on the same page.
- Keep regions inside the confirmed ROI above; if a caption or figure is
  partially outside, clip the bbox to the ROI.
- Use role "unknown" when uncertain instead of guessing.
- Never transcribe text. Never invent source element IDs.
- Regions should not overlap unless one contains the other (e.g., caption inside
  figure is still separate; avoid covering the whole page with one body block
  when the page clearly has multiple columns/blocks).
- Prefer at most 14 regions; merge same-role fragments into one region. Keep the
  JSON compact with no extra prose so it fits in the response limit."""


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        code = re.sub(r"^```(?:json)?\s*|\s*```$", "", code)
    return code


def _clip_bbox(bbox: dict, roi: dict | None) -> dict | None:
    if roi is None:
        return bbox
    try:
        x = max(float(bbox["x"]), float(roi["bbox"]["x"]))
        y = max(float(bbox["y"]), float(roi["bbox"]["y"]))
        right = min(float(bbox["x"]) + float(bbox["width"]), float(roi["bbox"]["x"]) + float(roi["bbox"]["width"]))
        bottom = min(float(bbox["y"]) + float(bbox["height"]), float(roi["bbox"]["y"]) + float(roi["bbox"]["height"]))
    except (KeyError, TypeError, ValueError):
        return bbox
    width = right - x
    height = bottom - y
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _regions_to_layout(task: LayoutTask, regions: list[dict], model: str) -> dict:
    roi = task.metadata.get("analysis_roi")
    normalized_regions = []
    for idx, region in enumerate(regions, start=1):
        content_class = region.get("content_class") or "unknown"
        role = region.get("role") or "unknown"
        bbox = region["bbox"]
        if content_class != "exclude":
            bbox = _clip_bbox(bbox, roi)
            if bbox is None:
                continue
        normalized_regions.append(
            {
                "region_id": str(region.get("region_id") or f"r{idx}"),
                "bbox": bbox,
                "content_class": content_class,
                "role": role,
                "order": region.get("order"),
                "source_candidate_ids": [],
                "source_element_ids": [],
                "parent_region_id": region.get("parent_region_id"),
                "confidence": region.get("confidence"),
            }
        )
    # Normalize reading order after any clipping/drop.
    ordered = [r for r in normalized_regions if r["content_class"] != "exclude"]
    ordered.sort(key=lambda r: (
        r["order"] if isinstance(r["order"], int) else 10**9,
        r["bbox"]["y"],
        r["bbox"]["x"],
    ))
    for new_order, r in enumerate(ordered, start=1):
        r["order"] = new_order

    # Deterministic action boilerplate: visual-direct requires one add action
    # per region, with exactly the same bbox.
    actions = []
    for idx, region in enumerate(normalized_regions, start=1):
        actions.append(
            {
                "action_id": f"a{idx}",
                "action": "add",
                "source_candidate_ids": [],
                "result_region_ids": [region["region_id"]],
                "bbox": region["bbox"],
                "target_region_id": None,
                "reason": "visual-direct region drawn from page image",
            }
        )

    layout = {
        "contract_version": "paperwright-final-layout-v0.1",
        "source_sha256": task.source_sha256,
        "page": task.page.to_dict(),
        "reviewer": f"paperwright-visual-review-bridge/{model}",
        "prompt_version": PROMPT_VERSION,
        "regions": normalized_regions,
        "actions": actions,
        "warnings": [],
    }
    FinalLayout.from_dict(layout)
    validate_layout_review(FinalLayout.from_dict(layout), task)
    return layout


_STRONG_CAPTION_LINE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table)\s+S?\d+[A-Za-z]?\s*(?:[|.:])",
    re.IGNORECASE,
)


def _ensure_caption_regions(
    task: LayoutTask,
    layout: dict,
    physical_document: dict,
) -> dict:
    """Add explicit caption regions for strong Figure/Table caption lines.

    The visual model occasionally labels a caption line as heading/body.  A
    small exact caption region wins geometry assignment during layout-apply,
    keeping explicit captions out of prose and avoiding a hard semantic
    failure there.
    """

    page = physical_document["pages"][task.page.page_index]
    page_w = task.page.width
    page_h = task.page.height
    caps = []
    for element in page["elements"]:
        if element.get("kind") != "text":
            continue
        text = element.get("text") or ""
        if not _STRONG_CAPTION_LINE.match(text):
            continue
        bbox = element["bbox"]
        caps.append(
            {
                "x": bbox["x"] / page_w,
                "y": bbox["y"] / page_h,
                "width": bbox["width"] / page_w,
                "height": bbox["height"] / page_h,
            }
        )
    if not caps:
        return layout

    caption_boxes = [
        region["bbox"]
        for region in layout["regions"]
        if region["content_class"] == "text" and region["role"] == "caption"
    ]

    def already_covered(cap):
        for box in caption_boxes:
            pad = 0.003
            if (
                cap["x"] >= box["x"] - pad
                and cap["y"] >= box["y"] - pad
                and cap["x"] + cap["width"] <= box["x"] + box["width"] + pad
                and cap["y"] + cap["height"] <= box["y"] + box["height"] + pad
            ):
                return True
        return False

    missing = [cap for cap in caps if not already_covered(cap)]
    if not missing:
        return layout

    x0 = min(cap["x"] for cap in missing)
    y0 = min(cap["y"] for cap in missing)
    x1 = max(cap["x"] + cap["width"] for cap in missing)
    y1 = max(cap["y"] + cap["height"] for cap in missing)
    pad = 0.006
    x0 = max(0.0, x0 - pad)
    y0 = max(0.0, y0 - pad)
    x1 = min(1.0, x1 + pad)
    y1 = min(1.0, y1 + pad)

    existing_ids = {region["region_id"] for region in layout["regions"]}
    rid = "r-caption-auto"
    suffix = 1
    while rid in existing_ids:
        suffix += 1
        rid = f"r-caption-auto-{suffix}"
    bbox = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
    layout["regions"].append(
        {
            "region_id": rid,
            "bbox": bbox,
            "content_class": "text",
            "role": "caption",
            "order": None,
            "source_candidate_ids": [],
            "source_element_ids": [],
            "parent_region_id": None,
            "confidence": 0.95,
        }
    )
    layout["actions"].append(
        {
            "action_id": f"a-{rid}",
            "action": "add",
            "source_candidate_ids": [],
            "result_region_ids": [rid],
            "bbox": bbox,
            "target_region_id": None,
            "reason": "auto caption separation for explicit Figure/Table caption",
        }
    )
    ordered = [r for r in layout["regions"] if r["content_class"] != "exclude"]
    ordered.sort(key=lambda r: (r["bbox"]["y"], r["bbox"]["x"]))
    for order, region in enumerate(ordered, start=1):
        region["order"] = order
    return layout


def _generate_layout(
    client, task: LayoutTask, image_path: Path, model: str, cost_report: CostReport
) -> dict:
    image_url = _data_url(image_path)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt(task)},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        temperature=0,
        max_tokens=4096,
        extra_body={"enable_thinking": False},
    )
    cost_report.record(
        bridge=f"paperwright-visual-review-bridge/{model}",
        model=model,
        step=f"page-{task.page.page_index + 1}",
        usage=getattr(resp, "usage", None),
    )
    content = resp.choices[0].message.content or ""
    content = _strip_fences(content)
    data = json.loads(content)
    if not isinstance(data.get("regions"), list):
        raise ValueError("模型输出缺少 regions 数组")
    return _regions_to_layout(task, data["regions"], model)


def _review_page(
    client,
    page_dir: Path,
    model: str,
    physical_document: dict,
    cost_report: CostReport,
    *,
    attempts: int = MAX_ATTEMPTS,
) -> dict:
    task = LayoutTask.from_dict(
        json.loads((page_dir / "layout-task.json").read_text(encoding="utf-8"))
    )
    image_path = page_dir / task.preview_filename
    if not image_path.is_file():
        raise SystemExit(f"缺少页面预览: {image_path}")
    last_error = None
    for _ in range(attempts):
        try:
            layout = _generate_layout(
                client, task, image_path, model, cost_report
            )
            layout = _ensure_caption_regions(task, layout, physical_document)
            validate_layout_review(FinalLayout.from_dict(layout), task)
            return layout
        except (ValueError, KeyError, json.JSONDecodeError, ContractValidationError) as exc:
            last_error = exc
            print(f"  校验失败: {exc}", file=sys.stderr)
            # fall through and retry; no explicit repair prompt so the model
            # gets a fresh sample on each attempt.
    raise SystemExit(f"视觉复核失败: {last_error}")


def main() -> int:
    ap = argparse.ArgumentParser()
    default_model = MODEL
    ap.add_argument("review_dir", type=Path)
    ap.add_argument("--pages", help="只处理指定页，如 1-3 或 1,5,9")
    ap.add_argument("--model", default=default_model, help=f"模型 ID（默认 {default_model}）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要处理的页面")
    args = ap.parse_args()

    review_dir = args.review_dir.expanduser().resolve()
    if not review_dir.is_dir():
        raise SystemExit(f"复核目录不存在: {review_dir}")
    page_dirs = sorted(
        path
        for path in review_dir.glob("page-*")
        if path.is_dir() and (path / "layout-task.json").is_file()
    )
    if not page_dirs:
        raise SystemExit("未在复核目录找到 page-*/layout-task.json")

    selected = _parse_pages(args.pages)
    if selected is not None:
        page_dirs = [path for path in page_dirs if int(path.name.split("-")[1]) in selected]
    if not page_dirs:
        raise SystemExit("没有匹配的页面")

    if args.dry_run:
        for path in page_dirs:
            print(path.name)
        return 0

    cost_path = review_dir / "visual-review-cost.json"
    if cost_path.exists():
        raise SystemExit(f"cost 报告已存在，拒绝覆盖: {cost_path}")

    client, base_url = _load_client()
    physical_path = review_dir / "extraction-cache" / "physical-document.json"
    if not physical_path.is_file():
        raise SystemExit(f"缺少物理文档缓存: {physical_path}")
    physical_document = json.loads(physical_path.read_text(encoding="utf-8"))
    print(f"视觉复核: {len(page_dirs)} 页, model={MODEL}, base_url={base_url}", file=sys.stderr)
    cost_report = CostReport()
    for path in page_dirs:
        output = path / "final-layout.json"
        if output.exists():
            print(f"{path.name}: final-layout.json 已存在，跳过", file=sys.stderr)
            continue
        print(f"{path.name}: 视觉复核中", file=sys.stderr)
        layout = _review_page(
            client, path, args.model, physical_document, cost_report
        )
        output.write_text(
            json.dumps(layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        print(f"{path.name}: 已写 final-layout.json", file=sys.stderr)
    cost_path.write_text(
        canonical_cost_report_json(cost_report),
        encoding="utf-8",
        newline="\n",
    )
    totals = cost_report.totals()
    print(
        f"用量: {totals['call_count']} 次调用, "
        f"{totals['input_tokens']} in / {totals['output_tokens']} out tokens, "
        f"估算 ${totals['estimated_cost_usd_known']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
