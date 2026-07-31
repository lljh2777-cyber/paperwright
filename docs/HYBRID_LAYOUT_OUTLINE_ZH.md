# Paper2MD 混合布局识别开发大纲

> 状态：A–E 已实现并验证；F 已实现训练数据导出，模型训练待积累真值
> 开发分支：`feature/hybrid-layout`
> 目标：以“规则生成候选、AI 审查布局、Paper2MD 精确执行”为第一阶段方案，并为后续本地机器学习保留数据。

## 1. 目标

- 正确划分正文、标题、Figure、Table、caption、页眉、页脚和边注。
- 为每个区块记录页面坐标、类型、阅读顺序和关联关系。
- 文字块读取 PDF 原生文字并重建段落。
- Figure/Table 作为完整视觉区域截图，不识别其内部图像文字。
- 前期由 AI 审查候选区块，后期由本地轻量模型处理高置信度页面。
- 任何不确定情况优先保留内容，避免静默丢失正文。

## 2. 非目标

- 不做 OCR。
- 不识别 Figure/Table 截图内部的文字。
- 不做公式 LaTeX 转换。
- 不做语义表格重建。
- 不让 AI 转录、改写论文正文或直接生成 Markdown。
- 第一阶段不训练端到端视觉神经网络。

## 3. 总体流程

```text
PDF
  ↓
PDFium 提取页面元素、坐标和预览图
  ↓
识别页面有效内容区，排除页眉/页脚/边注
  ↓
空白带和元素聚类规则生成候选区块
  ↓
计算候选区块及相邻关系特征
  ↓
AI 审查：保留、合并、拆分、补充、分类、排序、绑定
  ↓
Paper2MD 校验结果并吸附到精确 PDF 坐标
  ↓
文字块提取原生文字；视觉块整体截图
  ↓
生成 article.md + images/ + manifest.json
  ↓
保存候选、AI 动作和最终布局，供后续机器学习使用
```

## 4. 页面元素与坐标

使用 PDFium 获取：

- 页面宽高、旋转和坐标系；
- 原生文字字符、文字行及 bbox；
- 原生位图及 bbox；
- 矢量绘图及 bbox；
- 页面预览图。

内部坐标继续使用 PDF point；与 AI 交换时使用 `0–1` 归一化坐标。

### 4.1 原生文字与图像文字

- `native_text_coverage` 只统计 PDF 中已有的文字对象。
- 不对位图或截图内部文字执行 OCR。
- 扫描页没有原生文字时，`native_text_coverage` 必须为 `null`，不能记为零。
- Figure 内存在原生坐标轴文字时，只统计其几何特征，不单独转录输出。

## 5. 页面有效内容区（Content ROI）

结合位置、跨页重复和元素特征识别：

- 页眉；
- 页脚；
- 页码；
- 期刊名和日期；
- 重复边注或 running title。

脚注属于正文内容，不得按固定页边距直接删除。无法确定的外围内容应保留并降低置信度。

规则先生成保守的 `content_bbox`，AI 或人工通过完整页预览确认或调整。
这个 bbox 只是分析掩膜，不裁剪 PDF，不改变任何文字、图片、绘图或最终区块
的原始页面坐标。

规则提案除跨页重复文字外，还检测靠近页面上下边缘、与主体存在明显空白、
高度较小且占用稀疏的孤立带，用于排除期刊标签、DOI 横线、日期栏和页码。
宽而密集或多行的脚注不按该规则自动删除。

确认后：

- 完全位于 ROI 外的页眉、页脚、页码和装饰对象不进入候选生成；
- 与 ROI 边界相交的对象记录为 `boundary_crossing_element_ids`；
- 空白带、分栏、候选区块和分隔带只在 ROI 内计算；
- Figure/Table 仍从原 PDF 按原始页面坐标渲染。

Content ROI 应独立保存规则提案、最终 bbox、复核者和来源，既用于复现，也作为
后续正文窗口预测模型的训练数据。

## 6. 规则生成候选区块

### 6.1 占用图

根据文字行、位图和矢量绘图 bbox 建立页面占用图。图片 bbox 内部即使存在白色像素，也视为已占用。

### 6.2 分割顺序

1. 先检测横向空白带，划分正文带、视觉带、caption 带等。
2. 再在每个横向分区内检测纵向栏间空白。
3. 聚合相邻图片、绘图、面板标签和其他视觉元素。
4. 生成候选区块、候选分隔带和相邻关系。

优先采用相对阈值，例如中位行高、字符宽度和页面比例，避免固定像素阈值。

## 7. 候选区块特征

### 7.1 几何特征

- 归一化 bbox、宽高比和面积比；
- 距离页面四边的距离；
- 是否跨栏；
- 与前后区块的距离；
- 相邻区块的重叠、包含和对齐关系。

### 7.2 PDF 内部特征

- `native_text_available`；
- `native_text_coverage`；
- 规则文字行与分散文字的覆盖率；
- 文字行数量、平均行长、行距和行长方差；
- 字体、字号、粗体和旋转比例；
- 位图数量及覆盖率；
- 矢量路径、横线、竖线、矩形和网格特征。

### 7.3 少量模式特征

- 是否以 `Fig.`、`Figure` 或 `Table` 开头；
- 是否存在连续面板标签；
- 数字、刻度和百分号比例；
- 相邻区块字号、类型特征和距离。

模式匹配只读取 PDF 原生文字，不读取图片内部文字。

## 8. AI 布局审查

向 AI 提供：

- 页面预览图；
- 带编号候选框和分隔带的叠加图；
- 候选框坐标；
- 数值特征和相邻关系。

AI 只输出结构化布局计划，允许的动作包括：

```text
keep / merge / split / resize / discard / add / reorder / attach-caption
```

AI 需要标注：

- 区块类型；
- 页面内阅读顺序；
- 父子关系；
- Figure/Table 与 caption 的绑定；
- 应忽略的页面附属内容；
- 无法确定的区域。

第一阶段所有页面均由 AI 审查；积累数据后只审查低置信度或异常页面。

## 9. 程序校验与回退

Paper2MD 必须检查：

- bbox 是否位于页面范围内；
- 文字元素是否遗漏或重复分配；
- 分隔带是否切穿文字、图片或绘图；
- Figure/Table 是否裁剪完整；
- 阅读顺序是否唯一且完整；
- caption 是否与邻近视觉块合理绑定；
- 正文或脚注是否被误排除。

轻微坐标误差自动吸附到真实元素边界。校验失败时重新请求 AI；仍无法确定时保留内容并记录 `degraded`。

## 10. 输出规则

### 10.1 文字块

```text
区域内原生文字
  → 视觉行合并
  → 段落重建
  → Markdown
```

### 10.2 视觉块

Figure/Table 使用完整区域高分辨率渲染，保留位图、矢量图、坐标轴、标签和图例，不拆分识别内部文字。

### 10.3 Caption

Caption 使用原生文字提取，并与对应视觉资源绑定。需要同时支持 caption 位于视觉块上方或下方。

## 11. 数据留存

每页至少保存：

```text
candidates.json       脚本原始候选区块和特征
ai-actions.json       AI 的合并、拆分、分类和排序动作
final-layout.json     校验后的最终布局
preview.webp          可选的低分辨率页面预览
overlay.webp          可选的候选框叠加图
```

数据记录候选算法版本、特征模式版本、AI 模型和提示词版本。不设置复杂标签状态；人工发现错误时直接修正并保留简单变更记录。

默认不保存论文全文。敏感文档可以关闭页面预览留存。

## 12. 后续本地机器学习

第一批本地模型分别学习：

1. 候选空白带是否为有效边界；
2. 相邻候选框是否属于同一最终区域；
3. 区块属于 `exclude`、`text` 或 `visual`。

后续再扩展：

- `text → heading/body/caption/footnote`；
- `visual → figure/table/equation/other`；
- caption 配对排序；
- 页面是否需要 AI 审查。

`uncertain` 是低置信度处理状态，不作为普通训练标签。训练、验证和测试必须按整篇论文划分。

## 13. 分阶段实施

### 阶段 A：数据协议与可视化

- 定义候选区块、AI 动作和最终布局 schema。
- 导出页面预览、候选框叠加图和特征 JSON。
- 建立坐标转换、版本和路径安全检查。

### 阶段 B：确定性候选生成

- 实现页面有效内容区检测。
- 实现空白带、分栏和元素聚类。
- 使用合成页面覆盖单栏、双栏、跨栏标题、跨栏 Figure 和 caption。

### 阶段 C：AI 审查接口

- 定义严格的结构化输入输出协议。
- 支持候选框合并、拆分、补充、分类、排序和绑定。
- 对 AI 结果执行 schema 和几何校验。

### 阶段 D：Paper2MD 集成

- 文字块接入现有段落重建。
- Figure/Table 接入区域渲染。
- 扩展 manifest 的布局追溯信息。
- 默认模式保持兼容，新模式显式启用。

### 阶段 E：真实论文验证

- 覆盖不同期刊、栏数、语言和 Figure/Table 版式。
- 检查正文完整性、阅读顺序、视觉裁剪和 caption 绑定。
- 保存 AI 修改前后的训练数据。

### 阶段 F：本地模型

- 建立边界、合并和区块分类基线。
- 设置按错误代价区分的执行阈值。
- 高置信度直接执行，低置信度交给 AI。

当前只完成了确定性的训练数据导出。由于尚无足量
`final-layout.json` 真值，暂不训练或宣称已有可用分类器。

## 14. 第一阶段验收重点

- 不遗漏或重复正文文字。
- 双栏正文顺序正确。
- 跨栏 Figure/Table 不被拆散。
- Figure/Table 截图不裁掉内容。
- caption 与对应视觉块正确绑定。
- 页眉页脚不会进入正文，脚注不会被误删。
- AI 不转录或改写论文内容。
- 所有布局决策可在 manifest 和布局数据中追溯。
- 同一输入和同一布局计划生成确定性输出。

## 15. 当前 CLI 流程

### 15.1 生成 Content ROI 提案

```powershell
paper2md layout-prepare input.pdf roi-proposal-dir
```

先检查每页 `content-roi.png`。确认红框没有裁掉正文、脚注、Figure、Table 或
caption 后，修正根目录 `content-roi.json`，把 `review_status` 改为
`confirmed` 并填写 `reviewer`。

### 15.2 在确认的 ROI 内生成 AI 区块复核包

```powershell
paper2md layout-prepare input.pdf review-dir `
  --content-roi-json roi-proposal-dir/content-roi.json
```

每页生成：

- `layout-task.json`；
- `page.png`；
- `content-roi.png`；
- `overlay.png`；
- `review-instructions.md`。

视觉 AI 按说明只划分区块、标注类型和顺序，并在对应页面目录写入
`final-layout.json`；AI 不转录正文，也不读取 Figure/Table 内部文字。

### 15.3 校验并应用复核结果

```powershell
paper2md validate-final-layout review-dir/page-0001/final-layout.json `
  --task review-dir/page-0001/layout-task.json

paper2md layout-apply input.pdf review-dir output-dir --evidence standard
```

参考文献及行政性后置内容默认保留。需要时可显式选择：

```powershell
# 从 article.md 省略参考文献、致谢、作者贡献等后置内容
paper2md layout-apply input.pdf review-dir output-dir --references omit

# 将参考文献写入 references.md，并省略行政性后置内容
paper2md layout-apply input.pdf review-dir output-dir --references separate
```

检测优先使用 References、Bibliography 等章节标题，并兼容 PDF
原生文字层中被空格拆散的标题；标题缺失时，只在连续编号、年份、期刊或
DOI 等证据共同满足时才使用编号引文序列兜底。选择非 `keep` 模式后，致谢、
作者贡献、利益冲突、资助、作者信息和数据可用性等行政性后置内容也会省略；
Supplementary Information/Materials（补充材料）及其内容保留。未可靠检测到
参考文献时保持原文，不做猜测性删除。

默认输出为自包含文档包：

```text
output-dir/
├── article.md
├── images/
│   └── figure-0001.png
└── _paper2md/
    ├── run.json
    ├── source.json
    ├── manifest.json
    ├── 02-roi/
    │   └── content-roi.json
    ├── 03-layout/
    │   ├── page-0001-overlay.png
    │   └── page-0001-final-layout.json
    ├── 04-provenance/
    │   └── layout-provenance.json
    └── 05-validation/
        ├── validation-report.json
        └── validation-report.md
```

`--evidence minimal` 只保留 `article.md`、`images/` 和
`_paper2md/manifest.json`。`--evidence full` 还会增加
`01-physical/physical-document.json`、每页 `page.png`、Content ROI
预览和全部 `layout-task.json`，适合审计与训练数据积累。

原 PDF 默认不复制，只在 `source.json` 记录绝对路径、文件名、大小、页数和
SHA-256。显式使用 `--include-source-pdf` 时，才复制为
`_paper2md/source.pdf`。所有文件先写入同级临时目录，成功后再原子改名。

`standard` 和 `full` 的验证报告还包含自动输出质量检查：

- Markdown 疑似断词、重复词和异常短碎片；
- 疑似 Figure/Table 内标签混入正文；
- H1 标题是否完整且唯一；
- Markdown 图片链接是否有效，是否存在孤立图片；
- manifest 预期文件、实际文件和哈希清单是否一致；
- 候选正文文字对象是否未分配给任何有效区块；
- 同一 PDF 对象是否被多个最终区块重复使用；
- 原生文字重建是否遇到可疑 Unicode，及采用了哪些确定性修复。

`validation-report.json` 同时提供 `warning_summary`，按严重级别、检查项、
问题代码和底层警告代码聚合结果，并列出受影响页及最多 100 条可定位到
区块、段落或对象的 `actionable_findings`。Markdown 报告优先展示这些可操作
问题，不再要求人工逐条翻阅大量重复的底层警告。

文字模式识别属于启发式检查，只产生 `warning` 和可定位的片段；图片、
manifest 与对象映射属于确定性结构检查。AI 明确执行 `discard` 的对象单独计数，
不作为正文遗漏。

### 15.3 导出本地机器学习数据

```powershell
paper2md layout-export-dataset dataset-dir `
  --review-root reviewed-document-a `
  --review-root reviewed-document-b
```

训练目录只包含：

- 候选区块的数值特征和最终标签；
- merge/split/resize/discard 等动作；
- 阅读顺序区块对；
- caption 与视觉区块配对；
- 数据集 manifest 和文件哈希。

不包含论文正文、页面图像或底层文字元素 ID。数据按整篇论文 SHA-256
分组，后续训练/验证/测试必须继续按 `document_id` 划分。

## 16. 当前实现状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| A | 完成 | 布局任务、最终布局模型、schema、叠加图 |
| B | 完成 | 无 OCR 空白带、双栏、外围内容和特征生成 |
| C | 完成 | AI 复核说明、严格动作和完整性校验 |
| D | 完成 | 原生文字重建、视觉区块截图、manifest v0.6 |
| E | 完成 | 两篇真实论文共 29 页验证，双轮文件哈希一致 |
| F | 部分完成 | 数据导出完成；轻量分类器等待足量真值 |

真实样本统计和已知限制见
`docs/HYBRID_LAYOUT_STAGE_E_VALIDATION_ZH.md`。
