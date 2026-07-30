"""AI review protocol and deterministic validation for layout tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .exceptions import ContractValidationError
from .layout_models import FinalLayout, LayoutTask

LAYOUT_REVIEW_PROMPT_VERSION = "paper2md-layout-review-prompt-v0.1"


def build_layout_review_instructions(task: LayoutTask) -> str:
    """Build concise instructions for a visual AI layout reviewer."""

    return f"""# Paper2MD 页面布局审查

任务契约：`{task.contract_version}`
任务哈希：`{task.deterministic_sha256()}`
页面索引：`{task.page.page_index}`
候选区块：{len(task.candidates)}
候选分隔带：{len(task.separators)}
提示词版本：`{LAYOUT_REVIEW_PROMPT_VERSION}`

## 输入

- `{task.preview_filename}`：原始页面预览。
- `{task.overlay_filename}`：候选区块和分隔带编号叠加图。
- `layout-task.json`：精确坐标、元素来源和数值特征。

## 工作

1. 检查页眉、页脚、边注和正文是否分开。
2. 检查正文栏、跨栏标题、Figure/Table 和 caption 是否完整。
3. 对候选区块执行 keep、merge、split、resize、discard 或 add。
4. 标注 content_class、role、阅读顺序和父子关系。
5. 使用 attach-caption 记录 caption 与 Figure/Table 的关系。

## 禁止

- 不转录、改写或总结论文正文。
- 不识别图片内部文字。
- 不直接生成 Markdown。
- 不伪造候选区块、元素 ID 或 PDF 内容。

## 输出

将结果保存为 `final-layout.json`，内容只包含符合
`paper2md-final-layout-v0.1` 的 JSON。复制任务中的
`source_sha256` 和 `page`；`reviewer` 填实际模型名；`prompt_version`
必须为 `{LAYOUT_REVIEW_PROMPT_VERSION}`。

`source_element_ids` 必须输出空数组；Paper2MD 会根据候选来源和最终
bbox 自动分配真实元素，AI 不得填写或猜测元素 ID。

每个候选区块必须满足以下之一：

- 被一个最终区块引用；
- 通过 split 被多个最终区块引用；
- 通过 discard 明确排除。

非排除区块的 order 必须从 1 连续递增。无法判断时使用
content_class=`unknown`、role=`unknown`，并保留在阅读顺序中。
"""


def write_layout_review_instructions(
    output_path: str | Path,
    task: LayoutTask,
) -> Path:
    destination = Path(output_path)
    destination.write_text(
        build_layout_review_instructions(task),
        encoding="utf-8",
        newline="\n",
    )
    return destination


def _semantic_role_validation(layout: FinalLayout) -> None:
    text_roles = {"heading", "body", "caption", "footnote"}
    visual_roles = {"figure", "table", "equation"}
    exclude_roles = {"header", "footer", "margin"}
    by_id = {item.region_id: item for item in layout.regions}
    for region in layout.regions:
        if region.role in text_roles and region.content_class not in {
            "text",
            "unknown",
        }:
            raise ContractValidationError(
                f"{region.role} 必须是 text 或 unknown"
            )
        if region.role in visual_roles and region.content_class not in {
            "visual",
            "unknown",
        }:
            raise ContractValidationError(
                f"{region.role} 必须是 visual 或 unknown"
            )
        if region.role in exclude_roles and region.content_class != "exclude":
            raise ContractValidationError(
                f"{region.role} 必须使用 exclude"
            )
        if region.role == "caption" and region.parent_region_id is not None:
            parent = by_id[region.parent_region_id]
            if parent.content_class != "visual":
                raise ContractValidationError(
                    "caption 的父区块必须是 visual"
                )


def validate_layout_review(
    layout: FinalLayout,
    task: LayoutTask,
) -> None:
    """Validate AI review completeness, provenance, and semantic consistency."""

    layout.validate_against(task)
    if layout.prompt_version != LAYOUT_REVIEW_PROMPT_VERSION:
        raise ContractValidationError("AI 布局审查 prompt_version 不匹配")
    _semantic_role_validation(layout)

    known_candidates = {item.candidate_id for item in task.candidates}
    assignments: dict[str, list[str]] = {
        candidate_id: [] for candidate_id in known_candidates
    }
    for region in layout.regions:
        for candidate_id in region.source_candidate_ids:
            assignments[candidate_id].append(region.region_id)
        if region.source_element_ids:
            raise ContractValidationError(
                f"{region.region_id} 的 source_element_ids 必须由程序生成"
            )

    discarded: set[str] = set()
    split_candidates: set[str] = set()
    for action in layout.actions:
        if action.action == "discard":
            discarded.update(action.source_candidate_ids)
        elif action.action == "split":
            split_candidates.update(action.source_candidate_ids)

    for candidate_id, region_ids in assignments.items():
        if region_ids and candidate_id in discarded:
            raise ContractValidationError(
                f"{candidate_id} 不能同时被分配和 discard"
            )
        if not region_ids and candidate_id not in discarded:
            raise ContractValidationError(
                f"{candidate_id} 未被最终区块引用或 discard"
            )
        if len(region_ids) > 1 and candidate_id not in split_candidates:
            raise ContractValidationError(
                f"{candidate_id} 被多个区块引用但没有 split 动作"
            )


def load_and_validate_layout_review(
    layout_json: str | Path,
    task_json: str | Path,
) -> FinalLayout:
    task_value: Mapping[str, Any] = json.loads(
        Path(task_json).read_text(encoding="utf-8")
    )
    layout_value: Mapping[str, Any] = json.loads(
        Path(layout_json).read_text(encoding="utf-8")
    )
    task = LayoutTask.from_dict(task_value)
    layout = FinalLayout.from_dict(layout_value)
    validate_layout_review(layout, task)
    return layout
