# 随机跨页 Caption Holdout v0.5：事前协议

冻结日期：2026-08-17  
冻结基线：`e94cf33218e4df5a7ff8cb920d7e68fb271b2fc9`  
标签状态：未查看

## 目的

在未见论文上独立验证 `cross_page_panel_continuity` 修正，而不是再次用已经参与规则
设计的 v0.4 报告泛化结果。主要评估对象是确定性路由能否把真实的相邻页
Figure/Table–caption 关系送入 L2 候选集；视觉模型的最终接受/拒绝结果单独记录，不能
替代候选召回率。

## 抽样与停止规则

- 复用 v0.3 冻结的 669 篇 PMC OA 抽样框、seed `0x20260817` 和 xorshift32
  Fisher–Yates 候选顺序；不重新随机。
- v0.4 已检查候选位置 13–37，本轮从位置 **38** 开始顺序检查，直到纳入 **12 篇**
  合格论文后停止。
- 停止条件只由合格文档数决定，不因跨页正例数、路由输出、版式或期刊分布改变。
- 在 12 篇 PDF、资格记录和文件哈希全部冻结前，不运行 PaperWright，不生成页面预览，
  不搜索 Figure/Table caption，也不检查相邻页标签。

## 资格条件

资格条件与 v0.3/v0.4 保持一致：

- 同行评议的原始研究论文；
- born-digital PDF，具有可用的原生文字层；
- 官方 PMC OA article-version PDF 可获得；
- 未进入任何既有 PaperWright 开发、校准、holdout 或 challenge 语料。

综述、系统综述、病例报告、勘误、预印本、社论、研究方案等非原始研究排除。只能依据
文章类型、出版状态、文件可用性和 PDF 技术属性判断资格，不能依据图表数量、跨页迹象
或模型输出换样。

## 冻结评估流程

1. 保存逐候选资格决定、OA 元数据、官方 PDF URL、SHA-256、文件大小、页数和原生文字量。
2. 对冻结 PDF 使用基线提交和 `--extraction-profile standard` 生成完整 evidence；不得
   修改路由规则、候选阈值、caption 正则或 raster 参数。
3. 从 `issue-routing.json` 提取所有 `cross_page_caption_visual_binding` 页对，作为确定性
   候选预测；同一页对的多个 issue 只计一次。
4. 独立于路由输出生成每篇论文的全页 contact sheet，人工审计所有相邻页边界。真实正例
   定义为 Figure/Table 的视觉内容位于前页，而其显式 caption 首次开始于紧邻后页；若
   后页还含同一对象的后续面板或表格内容，仍计为正例。caption 仅从前页延续到后页不计。
5. 先冻结 silver 页对和不暴露 silver 位置的逐文档人工复核表；保留 `uncertain`，不强制
   二元裁决。人工签署后才生成 gold。
6. 在 gold 上报告 TP/FP/FN/TN、候选 precision/recall、文档级命中情况和自然 prevalence；
   pair-level 区间只作描述，并补充固定 seed 的 document-cluster bootstrap。

## 通过标准与边界

本轮不以单一“通过/失败”掩盖样本量：所有原始计数和区间都必须报告。预先指定的工程
目标是 gold 正例候选召回率不低于 90%，且 gold 负页对候选误报率不高于 2%；若正例少于
5 个，召回率只报告为探索性结果，不宣称达到泛化验收。最终 L2 关系质量按固定模型、prompt
版本和人工 gold 另表报告，不能和确定性候选指标混合。

任何依据 v0.5 标签进行的规则修改都会立即使本批转为开发/校准集。修改后的表现不得继续
称为独立 holdout 结果，必须从尚未检查的下一候选位置重新事前冻结新批次。

## 声明边界

v0.5 与 v0.3/v0.4 共用 2025-06-15 PMC OA 抽样框，因此只描述该明确抽样框中的
born-digital 原始研究论文，不代表所有科研出版物。文档是抽样单位，同一论文内的相邻
页对并非独立观测。
