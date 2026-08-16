# PaperWright 用户指南

本文面向**使用 paperwright 转换科研 PDF 的用户与 Agent**。开发者请阅读
[开发者指南](DEVELOPER_GUIDE.md)。

## 1. 默认只选择 Hybrid

面向科研论文的公开主入口是：

```bash
paperwright hybrid input.pdf output-dir
```

普通页与复杂页都走同一条流水线；普通页只执行 L0，出现有证据的局部问题才调用
L1/L2/L3。`convert`、`layout-*`、`text-*` 与 `tools/run_routing_plan.py` 保留用于兼容、
调试和协议开发，不再代表并列的产品方向。

原则：**能用规则就用规则；模型只做判断，校验器验收。**

## 2. 安装与验证

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install .
paperwright --version
paperwright --help
```

Agent 安装与 skills 复制见 [README](../README.md) 或
[Quickstart](QUICKSTART_ALPHA.md)。

## 3. Hybrid 运行与恢复

首次没有确认 ROI 时，命令会生成提案并返回 `awaiting_input`。不要直接改动作为历史
证据的提案文件；复制后审核：

```bash
cp output-dir.paperwright-run/layout-proposal/content-roi.json confirmed-roi.json
# 编辑 confirmed-roi.json：review_status=confirmed，并填写 reviewer
paperwright hybrid input.pdf output-dir \
  --resume --content-roi-json confirmed-roi.json
paperwright validate-hybrid-run output-dir.paperwright-run/run.json
```

已有确认 ROI 时可一次完成：

```bash
paperwright hybrid input.pdf output-dir --content-roi-json confirmed-roi.json
```

运行契约只记录阶段、哈希、下一动作和失败，不进行价格、成本或预算限制。详见
[Hybrid run contract](HYBRID_RUN_V0.2.md)。

v0.2 将运行拆成 `evidence/layout/projection/text/verification`。某阶段失败后使用同一命令
和 `--resume` 重试；已完成阶段的产物会先做哈希复核并被跳过。若前序产物被人工改动，
恢复会阻断，而不是悄悄接受漂移。

## 4. 兼容规则入口

```bash
paperwright convert input.pdf output-dir
```

- 输出目录必须不存在
- 输出：`article.md`、`images/`、`manifest.json`、`physical_document.json`，以及
  `_paperwright/completeness-report.json`
- 常用选项：
  - `--furniture auto|keep|strip`（默认 auto，自动剔除重复页眉页脚页码）
  - `--region-render-mode off|explicit|auto`（默认 off；auto 会保守图片化
    Figure、同页表格与独立公式）
  - `--references keep|omit|separate`（混合布局时）

## 5. 手动展开 Hybrid 阶段（高级）

### 5.1 生成 ROI 提案

```bash
paperwright layout-prepare input.pdf roi-review --extraction-profile fast
```

产出 `content-roi.json` 与每页 `content-roi.png`。

### 5.2 确认 ROI

人工或视觉模型检查每页红框是否覆盖正文、图、表、caption 且排除页眉页脚。
把 `content-roi.json` 中 `review_status` 改为 `confirmed` 并填写 `reviewer`。

### 5.3 生成布局复核包

```bash
paperwright layout-prepare input.pdf layout-review \
  --content-roi-json roi-review/content-roi.json \
  --review-mode visual-direct
```

此时生成语义主计划 `issue-routing.json`，每页固定以 `L0_RULE` 为基础，只将具体问题
路由到 `L1_TEXT_MODEL` / `L2_VISUAL_MODEL` / `L3_PROGRAM_SYNTHESIS` /
`HUMAN_REVIEW`。`routing.json` 仅保留给旧工具兼容。

### 5.4 执行路由计划（兼容执行器）

```bash
PYTHONPATH=src python tools/run_routing_plan.py \
  input.pdf layout-review output-dir
```

编排器会：

1. 所有页面先以 L0 处理；没有视觉/人工 issue 的页面生成规则 `final-layout.json`
2. L2 issue → 调 `tools/run_visual_review.py`，提示中只展开局部问题证据
3. HUMAN_REVIEW issue → 停止，要求人工完成
4. 全部布局校验通过后执行 `layout-apply`
5. 从 ArticleModel 精确发现 L1 block pair → L1 文本桥 → `text-package`
6. L1 失败 → 自动降级 L3 程序合成桥并写 manifest v0.11
7. Completeness finding → 写 `<output>.resolve-issues.json`，供局部返修

契约和两阶段发现方式见 [Issue-level Routing v0.1](ISSUE_ROUTING_V0.1.md)。也可以手动
逐阶段执行，见 [混合布局设计](HYBRID_LAYOUT_OUTLINE_ZH.md)。

### 5.5 视觉桥直连（MCP 超时时）

```bash
export DASHSCOPE_API_KEY=...
PYTHONPATH=src python tools/run_visual_review.py layout-review
PYTHONPATH=src python tools/run_visual_review.py layout-review --pages 1-3
```

默认 `--protocol auto`：有 `visual-relation-task.json` 时，模型只输出候选分组、角色、
阅读顺序与 caption 父子关系，程序确定性生成 `final-layout.json`；无候选时兼容回退旧
画框协议。可用 `--protocol relations` 禁止回退。关系审查另写
`visual-relation-review.json`。详见
[Visual Candidate Relations v0.1](VISUAL_RELATIONS_V0.1.md)。

页面布局完成后，编排器还会对“前一页视觉对象 + 后一页顶部 caption”生成 paired-page
关系任务。模型只能选择列出的 visual ref 或拒绝；结果见 review 根目录的
`cross-page-caption-*.json`。详见
[跨页 Figure/Table–caption 关系](CROSS_PAGE_CAPTION_V0.1.md)。

开发或校准自定义关系规则时，可用
`tools/validate_relation_dataset.py /path/to/dataset.json` 校验仓库外标注。模型辅助的
`silver` 数据不能冒充人工 `gold`；详见
[关系标注集契约](CAPTION_RELATION_DATASET_V0.1.md)。

### 5.6 应用布局

```bash
paperwright layout-apply input.pdf layout-review output-dir \
  --extraction-profile fast --evidence standard
```

输出是 manifest v0.9 自包含包：

```text
output-dir/
├── article.md
├── images/
└── _paperwright/
    ├── article-model.json
    ├── completeness-report.json
    ├── reader.json
    ├── manifest.json
    └── ...
```

无可用文字层但仍有图像/矢量证据的页面会自动保留为整页 PNG，不使用 OCR；真正空白页
会明确记录为 `source_page_blank`。`completeness-report.json` 若为 `warning`，请按其中
的页码检查孤立 caption 或疑似漏图。详见
[Completeness Gate v0.1](COMPLETENESS_GATE_V0.1.md)。

## 6. 文本复核

### 6.1 准备文本任务

```bash
paperwright text-prepare output-dir/_paperwright/article-model.json text-task.json
```

### 6.2 L1 纯文本桥（推荐先试）

```bash
PYTHONPATH=src python tools/run_text_review.py text-task.json text-review.json
paperwright validate-text-review text-review.json --task text-task.json
```

L1 支持：
- `replace-markdown`（format-only / dehyphenation）
- `join-blocks`（纯拼接，校验器强制）

### 6.3 L3 程序合成桥（L1 表达不了时）

```bash
PYTHONPATH=src python tools/run_text_synthesize.py \
  output-dir/_paperwright/article-model.json text-task.json text-review.json \
  --synthesis-run synthesize-run.json
```

L3 让模型写受限 DSL，由沙箱执行，守恒校验 + 重放校验。

### 6.4 生成文本复核派生包

```bash
# L1 结果 → v0.10 包
paperwright text-package output-dir text-task.json text-review.json reviewed-output

# L3 结果 → v0.11 包
paperwright text-package output-dir text-task.json text-review.json reviewed-output \
  --synthesis-run synthesize-run.json

paperwright validate-text-package reviewed-output
```

## 7. Bridge 原始 usage（兼容观测）

现有三个桥会保留供应商 usage 报告，供 evaluation 离线比较模型。它们不是 Hybrid
run contract 的预算或准入条件；核心不维护价格，也不按 token 停止流水线。

| 桥 | 报告 |
|---|---|
| L1 文本桥 | `<review>.usage.json` |
| L3 程序合成桥 | `synthesize-cost.json` |
| 视觉桥 | `visual-review-cost.json` |

报告字段：`call_count`、`input_tokens`、`output_tokens`、
`reasoning_tokens`、`estimated_cost_usd_known`、逐次调用记录。

## 8. 批量转换

```bash
paperwright batch output-root --input-dir pdf-directory --continue-on-error
```

只扫描第一层 PDF；每篇独立原子输出；生成 `batch_summary.json`。

## 9. 输出校验

```bash
paperwright validate-article-model output-dir/_paperwright/article-model.json
paperwright validate-reader output-dir/_paperwright/reader.json
paperwright validate-text-package reviewed-output   # 仅 v0.10/v0.11 包
```

## 10. 常见问题

见 [故障排查](TROUBLESHOOTING.md) 和 [支持矩阵](SUPPORT_MATRIX.md)。
扫描版 PDF 无 OCR；表格语义行列不重建；公式不做 LaTeX。
