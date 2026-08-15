# PaperWright 用户指南

本文面向**使用 paperwright 转换科研 PDF 的用户与 Agent**。开发者请阅读
[开发者指南](DEVELOPER_GUIDE.md)。

## 1. 选择哪种工作流

| 场景 | 工作流 | 是否调用模型 |
|---|---|---|
| 普通双栏论文，只想快速拿到 Markdown | `convert` 直接转换 | 否 |
| 多篇普通论文 | `batch` 批量转换 | 否 |
| 复杂版面、跨栏图/表、要求语义阅读顺序 | `layout-*` 混合布局复核 | 可选视觉模型/人工 |
| 转换后需要整理断句、断行、段落拼接 | `text-*` 文本复核 | 可选纯文本模型 |
| 声明式文本复核表达不了的长尾版式 | L3 程序合成桥 | 可选纯文本模型 |

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

## 3. 直接转换

```bash
paperwright convert input.pdf output-dir
```

- 输出目录必须不存在
- 输出：`article.md`、`images/`、`manifest.json`、`physical_document.json`
- 常用选项：
  - `--furniture auto|keep|strip`（默认 auto，自动剔除重复页眉页脚页码）
  - `--region-render-mode off|explicit|auto`（默认 off；auto 会保守图片化
    Figure、同页表格与独立公式）
  - `--references keep|omit|separate`（混合布局时）

## 4. 混合布局（视觉复核）

### 4.1 生成 ROI 提案

```bash
paperwright layout-prepare input.pdf roi-review --extraction-profile fast
```

产出 `content-roi.json` 与每页 `content-roi.png`。

### 4.2 确认 ROI

人工或视觉模型检查每页红框是否覆盖正文、图、表、caption 且排除页眉页脚。
把 `content-roi.json` 中 `review_status` 改为 `confirmed` 并填写 `reviewer`。

### 4.3 生成 visual-direct 复核包

```bash
paperwright layout-prepare input.pdf layout-review \
  --content-roi-json roi-review/content-roi.json \
  --review-mode visual-direct
```

此时生成 `routing.json`：每页被确定性路由为
`L0_RULE` / `L1_TEXT_MODEL` / `L2_VISUAL_MODEL` / `HUMAN_REVIEW`。

### 4.4 执行路由计划（推荐）

```bash
PYTHONPATH=src python tools/run_routing_plan.py \
  input.pdf layout-review output-dir \
  --token-budget 200000
```

编排器会：

1. L0 页 → 用确认 ROI 生成规则兜底 `final-layout.json`
2. L2 页 → 调 `tools/run_visual_review.py` 让视觉模型画区域
3. HUMAN_REVIEW 页 → 停止，要求人工完成
4. 全部布局校验通过后执行 `layout-apply`
5. L1 页 → `text-prepare` → L1 文本桥 → `text-package`
6. L1 失败 → 自动降级 L3 程序合成桥并写 manifest v0.11

也可以手动逐阶段执行，见 [混合布局设计](HYBRID_LAYOUT_OUTLINE_ZH.md)。

### 4.5 视觉桥直连（MCP 超时时）

```bash
export DASHSCOPE_API_KEY=...
PYTHONPATH=src python tools/run_visual_review.py layout-review
PYTHONPATH=src python tools/run_visual_review.py layout-review --pages 1-3
```

它会为每页写 `final-layout.json` 并写 `visual-review-cost.json`。

### 4.6 应用布局

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
    ├── reader.json
    ├── manifest.json
    └── ...
```

## 5. 文本复核

### 5.1 准备文本任务

```bash
paperwright text-prepare output-dir/_paperwright/article-model.json text-task.json
```

### 5.2 L1 纯文本桥（推荐先试）

```bash
PYTHONPATH=src python tools/run_text_review.py text-task.json text-review.json
paperwright validate-text-review text-review.json --task text-task.json
```

L1 支持：
- `replace-markdown`（format-only / dehyphenation）
- `join-blocks`（纯拼接，校验器强制）

### 5.3 L3 程序合成桥（L1 表达不了时）

```bash
PYTHONPATH=src python tools/run_text_synthesize.py \
  output-dir/_paperwright/article-model.json text-task.json text-review.json \
  --synthesis-run synthesize-run.json
```

L3 让模型写受限 DSL，由沙箱执行，守恒校验 + 重放校验。

### 5.4 生成文本复核派生包

```bash
# L1 结果 → v0.10 包
paperwright text-package output-dir text-task.json text-review.json reviewed-output

# L3 结果 → v0.11 包
paperwright text-package output-dir text-task.json text-review.json reviewed-output \
  --synthesis-run synthesize-run.json

paperwright validate-text-package reviewed-output
```

## 6. 成本计量

三个桥都会写 usage 报告：

| 桥 | 报告 |
|---|---|
| L1 文本桥 | `<review>.usage.json` |
| L3 程序合成桥 | `synthesize-cost.json` |
| 视觉桥 | `visual-review-cost.json` |

报告字段：`call_count`、`input_tokens`、`output_tokens`、
`reasoning_tokens`、`estimated_cost_usd_known`、逐次调用记录。

## 7. 批量转换

```bash
paperwright batch output-root --input-dir pdf-directory --continue-on-error
```

只扫描第一层 PDF；每篇独立原子输出；生成 `batch_summary.json`。

## 8. 输出校验

```bash
paperwright validate-article-model output-dir/_paperwright/article-model.json
paperwright validate-reader output-dir/_paperwright/reader.json
paperwright validate-text-package reviewed-output   # 仅 v0.10/v0.11 包
```

## 9. 常见问题

见 [故障排查](TROUBLESHOOTING.md) 和 [支持矩阵](SUPPORT_MATRIX.md)。
扫描版 PDF 无 OCR；表格语义行列不重建；公式不做 LaTeX。
