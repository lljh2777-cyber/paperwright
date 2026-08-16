# PaperWright 科研论文质量基线协议 v0.1

> 日期：2026-08-16  
> 适用范围：born-digital 科研论文；评测对象是 PaperWright 当前混合重建能力  
> 非目标：模型价格、费用预算、OCR、语义表格还原和公式 LaTeX 转写

本协议用于比较 PDF 原始证据、PaperWright 产物和人工/AI 审阅结论。评测结论必须
指向具体页面和可观察证据；不能仅用“效果不错”或“排版较差”等主观描述。

## 1. 评测单元

第一版采用两种互补单元：

- **文档级检查**：标题、章节连续性、参考文献边界、资产完整性、异常终止；
- **页面级抽样**：每篇至少检查首页、一个普通正文页、两个高风险页和一个末尾/
  参考文献页。少于五页的论文检查全部页面。

自动检查覆盖全部页面；人工/AI 视觉检查采用上述分层抽样。发现系统性错误时，应继续
检查相邻页，并记录受影响的页码范围。

## 2. 原始事实标注

每篇论文至少记录：

- 文件名、SHA-256、页数和原生文字层是否可用；
- 主要版式：单栏、双栏、三栏或混合；
- 是否出现跨栏 Figure/Table、子图、display equation、脚注、页眉页脚；
- 是否有补充材料、附录或参考文献；
- 选中页面的正确阅读顺序与主要视觉对象/caption 关系；
- 任何无法由页面证据可靠判断的内容。

标注只描述结构事实，不转写或评价论文科学内容。

## 3. 质量维度

| 维度 | 判定重点 |
|---|---|
| `text_integrity` | 是否遗漏、重复、串栏、错误断词或无依据增字 |
| `reading_order` | 标题、正文栏、跨栏对象和页间内容顺序是否正确 |
| `section_structure` | 标题、摘要、章节、参考文献等层级与边界是否正确 |
| `visual_completeness` | Figure、Table、display equation 是否完整保留且裁切合理 |
| `caption_binding` | caption 是否完整、独立并绑定到正确视觉对象 |
| `furniture_exclusion` | 页眉、页脚、页码等是否正确排除且不误删正文 |
| `provenance` | 输出块和资产是否具有可回溯的页码、bbox/元素证据 |
| `uncertainty_handling` | 证据不足时是否显式 suspicious/human_required，而非猜测 |

每个维度采用四级结果：

- `pass`：抽样证据中未发现实质问题；
- `minor`：局部可读性问题，不改变内容或主要关系；
- `major`：遗漏、重复、串栏、错误绑定或大范围结构错误；
- `not_assessed`：本轮没有足够证据，不得按通过计算。

另外单独记录 `hallucination_count`。任何模型无来源生成或实质改写正文，都属于阻断性
错误，不能被平均分掩盖。

## 4. Issue 记录

每个问题至少包含：

```json
{
  "page": 7,
  "category": "caption_binding",
  "severity": "major",
  "source_evidence": "Figure 3 caption is below the full-width panel",
  "observed_output": "caption appears as body text before the left column",
  "likely_layer": "routing",
  "recommended_action": "request_visual",
  "confidence": "high"
}
```

`likely_layer` 只能取：`extraction`、`evidence`、`routing`、`rule`、`text_model`、
`visual_model`、`validation`、`projection`、`unknown`。

`recommended_action` 优先从以下集合选择：

```text
keep_rule
join_blocks
split_block
reorder
bind_caption
exclude_furniture
render_visual
request_text_judgment
request_visual
paper_recipe
human_required
```

## 5. 失败归因

问题按“最早可以阻止错误的层”归因，而不是按最终看见错误的位置归因：

- 原生对象没有被读取：`extraction`；
- 已读取但候选或信号缺失：`evidence`；
- 证据充分但没有升级正确处理器：`routing`；
- 通用规则产生错误关系：`rule`；
- 模型判断错误：对应 `text_model` 或 `visual_model`；
- 非法/低置信结果未被拒绝：`validation`；
- Article Model 正确但 Markdown/Reader 错误：`projection`。

无法可靠定位时写 `unknown`，不得强行归因。

## 6. 基线汇总

基线报告至少给出：

- 成功转换文档数、失败文档数和未评估文档数；
- 各质量维度的 `pass/minor/major/not_assessed` 文档数；
- 每 100 个抽样页的 issue 数；
- hallucination、正文遗漏、重复、串栏和错误 caption 绑定次数；
- 各 `likely_layer` 与 `recommended_action` 的频次；
- 当前路由建议与审阅事实之间的混淆情况；
- 最高频的两个失败簇及其建议工程优先级。

Token、延迟和模型标识可以作为原始观测字段保留，但不计算价格、不设置预算，也不把
不同供应商 token 当作可以直接互换的统一单位。

## 7. 存储约束

真实 PDF、页面截图、完整转换结果、全文 gold 和包含论文正文的 agent 中间产物放在
仓库外。仓库中只允许保存本协议、去正文的聚合指标、失败分类、哈希与必要的短证据
描述，并继续遵守 `docs/STORAGE_POLICY.md`。
