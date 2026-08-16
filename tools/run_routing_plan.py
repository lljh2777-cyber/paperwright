#!/usr/bin/env python3
"""Execute a deterministic routing plan across PaperWright bridges.

Inputs:
- review_dir prepared by `paperwright layout-prepare` (contains routing.json)
- input_pdf and an output directory

The orchestrator:
1. fills L0-routed pages with a deterministic body+caption fallback layout;
2. sends L2-routed pages to tools/run_visual_review.py;
3. stops immediately on any HUMAN_REVIEW page and asks for human completion;
4. validates every final-layout and runs `paperwright layout-apply`;
5. if any page was routed to L1_TEXT_MODEL, runs text-prepare, the L1 bridge
   and text-package into a manifest v0.10 derivative.

The orchestrator itself never calls a model or network API.  It only launches
the same optional bridge scripts a human/agent would run, and honors a simple
token budget from the bridges' usage reports.

Usage:
    PYTHONPATH=src python tools/run_routing_plan.py input.pdf review-dir out-dir
    PYTHONPATH=src python tools/run_routing_plan.py input.pdf review-dir out-dir --token-budget 200000 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from paperwright.auto_layout import build_l0_final_layout
from paperwright.layout_models import LayoutTask, FinalLayout
from paperwright.layout_review import validate_layout_review
from paperwright.issue_routing import (
    ISSUE_ROUTING_CONTRACT_VERSION,
    refine_issue_routing,
    refine_issue_routing_with_text_task,
    validate_issue_routing,
)
from paperwright.routing import (
    ROUTING_CONTRACT_VERSION,
    ROUTE_HUMAN_REVIEW,
    ROUTE_L0_RULE,
    ROUTE_L1_TEXT_MODEL,
    ROUTE_L2_VISUAL_MODEL,
    ROUTE_L3_PROGRAM_SYNTHESIS,
)

ROOT = Path(__file__).resolve().parents[1]


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _cmd_python(*argv: str) -> list[str]:
    return [sys.executable, *argv]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + current if current else "")
    return env


def _run(argv: list[str], *, label: str, cwd: Path | None = None) -> None:
    _log(f"  + {label}")
    if cwd is None:
        cwd = ROOT
    process = subprocess.run(
        argv,
        cwd=cwd,
        env=_env(),
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if process.stdout.strip():
        _log(process.stdout.strip()[-4000:])
    if process.stderr.strip():
        _log(process.stderr.strip()[-4000:])
    if process.returncode != 0:
        raise SystemExit(f"{label} 失败 (exit {process.returncode})")


def _try_run(argv: list[str], *, label: str, cwd: Path | None = None) -> bool:
    try:
        _run(argv, label=label, cwd=cwd)
        return True
    except SystemExit as exc:
        _log(f"{label} 未成功: {exc}")
        return False


def _read_routing(review_dir: Path) -> dict:
    path = review_dir / "routing.json"
    if not path.is_file():
        raise SystemExit(f"缺少 routing.json: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("contract_version") != ROUTING_CONTRACT_VERSION:
        raise SystemExit("routing.json 契约版本不支持")
    return value


def _page_group(routing: dict, route: str) -> list[int]:
    return sorted(
        page["page_index"] + 1
        for page in routing["pages"]
        if page["route"] == route
    )


def _read_issue_routing(review_dir: Path) -> dict | None:
    path = review_dir / "issue-routing.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("contract_version") != ISSUE_ROUTING_CONTRACT_VERSION:
        raise SystemExit("issue-routing.json 契约版本不支持")
    try:
        validate_issue_routing(value)
    except Exception as exc:
        raise SystemExit(f"issue-routing.json 校验失败: {exc}") from exc
    return value


def _issue_page_group(plan: dict, route: str) -> list[int]:
    pages: set[int] = set()
    for issue in plan["issues"]:
        if issue["status"] != "open" or issue["route"] != route:
            continue
        pages.add(issue["page_index"] + 1)
        pages.update(
            page_index + 1
            for page_index in issue["scope"].get(
                "related_page_indices", ()
            )
        )
    return sorted(pages)


def _usage_tokens(report_path: Path) -> int:
    if not report_path.is_file():
        return 0
    value = json.loads(report_path.read_text(encoding="utf-8"))
    totals = value.get("totals", {})
    return int(totals.get("input_tokens", 0)) + int(
        totals.get("output_tokens", 0)
    )


def _check_budget(used_tokens: int, token_budget: int | None) -> None:
    if token_budget is not None and used_tokens > token_budget:
        raise SystemExit(
            f"token 预算超限: 已用 {used_tokens} > 预算 {token_budget}，"
            "停止执行并回退人工/规则路径"
        )


def _fill_l0_pages(
    review_dir: Path,
    page_numbers: list[int],
) -> None:
    if not page_numbers:
        return
    physical = json.loads(
        (review_dir / "extraction-cache" / "physical-document.json").read_text(
            encoding="utf-8"
        )
    )
    from paperwright.models import Page as PhysicalPage

    pages = {
        item["page_index"]: PhysicalPage.from_dict(item)
        for item in physical["pages"]
    }
    for page_number in page_numbers:
        page_dir = review_dir / f"page-{page_number:04d}"
        output = page_dir / "final-layout.json"
        if output.exists():
            continue
        task = LayoutTask.from_dict(
            json.loads((page_dir / "layout-task.json").read_text(encoding="utf-8"))
        )
        layout = build_l0_final_layout(task, pages[task.page.page_index])
        validate_layout_review(FinalLayout.from_dict(layout), task)
        output.write_text(
            json.dumps(layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        _log(f"{page_dir.name}: L0 规则兜底布局已写")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf", type=Path)
    ap.add_argument("review_dir", type=Path)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--reviewed-output", type=Path, default=None)
    ap.add_argument("--evidence", default="standard")
    ap.add_argument("--references", default="keep")
    ap.add_argument("--extraction-profile", default=None)
    ap.add_argument("--token-budget", type=int, default=None)
    ap.add_argument("--text-task", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    review_dir = args.review_dir.expanduser().resolve()
    issue_routing = _read_issue_routing(review_dir)
    if issue_routing is not None:
        all_pages = set(range(1, issue_routing["page_count"] + 1))
        l1_pages = _issue_page_group(issue_routing, ROUTE_L1_TEXT_MODEL)
        l2_pages = _issue_page_group(issue_routing, ROUTE_L2_VISUAL_MODEL)
        l3_pages = _issue_page_group(
            issue_routing,
            ROUTE_L3_PROGRAM_SYNTHESIS,
        )
        human_pages = _issue_page_group(issue_routing, ROUTE_HUMAN_REVIEW)
        l0_pages = sorted(all_pages - set(l2_pages) - set(human_pages))
        _log(
            "使用 issue-routing.json："
            f"{issue_routing['summary']['issue_count']} 个局部 issue，"
            "页面仅作为现有桥的兼容执行适配器"
        )
    else:
        routing = _read_routing(review_dir)
        l0_pages = _page_group(routing, ROUTE_L0_RULE)
        l1_pages = _page_group(routing, ROUTE_L1_TEXT_MODEL)
        l2_pages = _page_group(routing, ROUTE_L2_VISUAL_MODEL)
        l3_pages = _page_group(routing, ROUTE_L3_PROGRAM_SYNTHESIS)
        human_pages = _page_group(routing, ROUTE_HUMAN_REVIEW)

    _log(
        "路由计划: "
        f"L0={len(l0_pages)} L1={len(l1_pages)} L2={len(l2_pages)} "
        f"L3={len(l3_pages)} HUMAN={len(human_pages)}"
    )
    if human_pages:
        raise SystemExit(
            f"HUMAN_REVIEW 页面需要人工完成 final-layout，停止执行: {human_pages}"
        )
    if l3_pages and issue_routing is None:
        _log("L3 页面在布局阶段先走 L2；文本阶段再考虑 L3。")
        l2_pages = sorted(set(l2_pages) | set(l3_pages))

    # Visual stage
    if args.dry_run:
        print("visual-pages", ",".join(str(p) for p in l2_pages))
        print("l0-pages", ",".join(str(p) for p in l0_pages))
        print("l1-pages", ",".join(str(p) for p in l1_pages))
        return 0

    _fill_l0_pages(review_dir, sorted(set(l0_pages) | set(l1_pages)))
    if l2_pages:
        pages_arg = ",".join(str(page) for page in l2_pages)
        _run(
            _cmd_python(
                str(ROOT / "tools" / "run_visual_review.py"),
                str(review_dir),
                "--pages",
                pages_arg,
                "--protocol",
                "auto",
                *(
                    [
                        "--issue-routing",
                        str(review_dir / "issue-routing.json"),
                    ]
                    if issue_routing is not None
                    else []
                ),
            ),
            label=f"视觉桥 pages={pages_arg}",
        )
        _check_budget(
            _usage_tokens(review_dir / "visual-review-cost.json"),
            args.token_budget,
        )

    # Validate every final layout before layout-apply.
    for page_dir in sorted(review_dir.glob("page-*")):
        layout_path = page_dir / "final-layout.json"
        task_path = page_dir / "layout-task.json"
        if not layout_path.is_file() or not task_path.is_file():
            raise SystemExit(f"缺少布局产物: {page_dir.name}")
        task = LayoutTask.from_dict(
            json.loads(task_path.read_text(encoding="utf-8"))
        )
        layout = FinalLayout.from_dict(
            json.loads(layout_path.read_text(encoding="utf-8"))
        )
        validate_layout_review(layout, task)
    _log("全部 final-layout.json 校验通过")

    _run(
        _cmd_python(
            str(ROOT / "tools" / "run_cross_page_caption_review.py"),
            str(review_dir),
        ),
        label="跨页 Figure/Table caption 关系复核",
    )

    layout_apply_argv = _cmd_python(
        "-m",
        "paperwright",
        "layout-apply",
        str(args.input_pdf),
        str(review_dir),
        str(args.output_dir),
        "--evidence",
        args.evidence,
        "--references",
        args.references,
    )
    if args.extraction_profile:
        layout_apply_argv.extend(
            ["--extraction-profile", args.extraction_profile]
        )
    _run(layout_apply_argv, label="layout-apply")

    article_model = args.output_dir / "_paperwright" / "article-model.json"
    task_path = args.text_task or (args.output_dir / "text-task.json")
    text_task_prepared = False
    execution_issue_plan = issue_routing
    resolution_path: Path | None = None

    # Paragraph boundaries only become trustworthy after layout projection.
    # Discover exact validator-eligible pairs from ArticleModel/TextTask rather
    # than guessing from raw PDF fragments during layout-prepare.
    if issue_routing is not None and article_model.is_file():
        _run(
            _cmd_python(
                "-m",
                "paperwright",
                "text-prepare",
                str(article_model),
                str(task_path),
            ),
            label="text-prepare (局部 issue 发现)",
        )
        text_task_prepared = True
        model_value = json.loads(article_model.read_text(encoding="utf-8"))
        task_value = json.loads(task_path.read_text(encoding="utf-8"))
        execution_issue_plan = refine_issue_routing_with_text_task(
            issue_routing,
            task_value,
            model_value,
        ).to_dict()

    # Feed completeness findings into the same separate resolution plan. The
    # published output package remains immutable.
    if issue_routing is not None:
        completeness_path = (
            args.output_dir / "_paperwright" / "completeness-report.json"
        )
        if completeness_path.is_file():
            completeness = json.loads(
                completeness_path.read_text(encoding="utf-8")
            )
            execution_issue_plan = refine_issue_routing(
                execution_issue_plan or issue_routing,
                completeness,
            ).to_dict()

        original_ids = {
            item["issue_id"] for item in issue_routing["issues"]
        }
        added = [
            item
            for item in (execution_issue_plan or issue_routing)["issues"]
            if item["issue_id"] not in original_ids
        ]
        if added:
            resolution_path = args.output_dir.parent / (
                f"{args.output_dir.name}.resolve-issues.json"
            )
            if resolution_path.exists():
                raise SystemExit(
                    f"局部 resolution plan 已存在，拒绝覆盖: {resolution_path}"
                )
            resolution_path.write_text(
                json.dumps(
                    execution_issue_plan,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            by_route: dict[str, int] = {}
            for item in added:
                by_route[item["route"]] = by_route.get(item["route"], 0) + 1
            _log(
                f"投影后发现 {len(added)} 个局部 issue "
                f"({by_route}): {resolution_path}"
            )

        l1_pages = _issue_page_group(
            execution_issue_plan or issue_routing,
            ROUTE_L1_TEXT_MODEL,
        )
        l3_pages = _issue_page_group(
            execution_issue_plan or issue_routing,
            ROUTE_L3_PROGRAM_SYNTHESIS,
        )

    # Text stage
    text_pages = sorted(set(l1_pages) | set(l3_pages))
    if not text_pages:
        _log("无 L1/L3 页面，跳过文本模型阶段")
        return 0

    if not article_model.is_file():
        raise SystemExit("文本 issue 已存在，但输出缺少 article-model.json")
    reviewed_output = args.reviewed_output or (
        args.output_dir.parent / f"{args.output_dir.name}-text-reviewed"
    )
    if not text_task_prepared:
        _run(
            _cmd_python(
                "-m",
                "paperwright",
                "text-prepare",
                str(article_model),
                str(task_path),
            ),
            label="text-prepare",
        )

    used_l1 = False
    if l1_pages:
        review_path = args.output_dir / "text-review.json"
        l1_ok = _try_run(
            _cmd_python(
                str(ROOT / "tools" / "run_text_review.py"),
                str(task_path),
                str(review_path),
                "--pages",
                ",".join(str(page) for page in l1_pages),
                *(
                    [
                        "--issue-routing",
                        str(
                            resolution_path
                            or review_dir / "issue-routing.json"
                        ),
                        "--article-model",
                        str(article_model),
                    ]
                    if issue_routing is not None
                    else []
                ),
            ),
            label=f"L1 文本桥 pages={','.join(str(p) for p in l1_pages)}",
        )
        _check_budget(
            _usage_tokens(review_path.with_name(review_path.stem + ".usage.json")),
            args.token_budget,
        )
        if l1_ok:
            l1_ok = _try_run(
                _cmd_python(
                    "-m",
                    "paperwright",
                    "validate-text-review",
                    str(review_path),
                    "--task",
                    str(task_path),
                ),
                label="validate-text-review",
            )
        used_l1 = l1_ok

    synthesis_run_path: Path | None = None
    if used_l1:
        review_path = args.output_dir / "text-review.json"
    else:
        _log("L1 未成功，降级 L3 程序合成桥")
        review_path = args.output_dir / "text-review.l3.json"
        synthesis_run_path = args.output_dir / "synthesize-run.json"
        l3_pages = sorted(set(l3_pages) | set(l1_pages))
        _run(
            _cmd_python(
                str(ROOT / "tools" / "run_text_synthesize.py"),
                str(article_model),
                str(task_path),
                str(review_path),
                "--pages",
                ",".join(str(page) for page in l3_pages),
                "--synthesis-run",
                str(synthesis_run_path),
            ),
            label=f"L3 程序合成桥 pages={','.join(str(p) for p in l3_pages)}",
        )
        _check_budget(
            _usage_tokens(
                synthesis_run_path.with_name("synthesize-cost.json")
            ),
            args.token_budget,
        )
        _run(
            _cmd_python(
                "-m",
                "paperwright",
                "validate-text-review",
                str(review_path),
                "--task",
                str(task_path),
            ),
            label="validate-text-review",
        )

    package_argv = _cmd_python(
        "-m",
        "paperwright",
        "text-package",
        str(args.output_dir),
        str(task_path),
        str(review_path),
        str(reviewed_output),
    )
    if synthesis_run_path is not None:
        package_argv.extend(["--synthesis-run", str(synthesis_run_path)])
    _run(package_argv, label="text-package")
    _log(f"文本复核派生包: {reviewed_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
