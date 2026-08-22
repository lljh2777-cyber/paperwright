# GROBID 科研语义证据评估 v0.1：事前协议

冻结日期：2026-08-22

冻结基线：`ff8959f714262bef0ac8401cff0ed9d092a1d1a2`

标签状态：未运行 PaperWright/GROBID，未查看候选 PDF 页面

## 目的

本评估回答一个窄问题：在 born-digital 科研论文上，GROBID `0.9.0-crf` 提交的哪些语义
claims 能为 PaperWright 粗提取提供可靠增量，哪些只能作为待复核提示。

真实 HTTP 集成已验通，但 observations、claims 或 alignments 的数量不等于语义质量。
本轮不评估 GROBID Markdown，不比较模型成本，也不调用文本或视觉大模型。

## 语料冻结

- 复用 random holdout v0.3 已冻结的 669 篇 PMC OA 抽样框和
  `xorshift32 Fisher–Yates, seed 0x20260817` 候选顺序；不重新随机。
- 原 unseen L2 v0.1 已检查到位置 64；本轮从一基位置 **65** 开始，顺序检查到纳入
  **8 篇**合格论文后立即停止。
- 纳入条件：同行评议原始研究、官方 PMC OA article-version PDF 可用、born-digital、
  原生文字层可用、未进入任何既有 PaperWright 开发/校准/challenge/holdout 语料。
- 排除综述、系统综述、病例报告、临床图片、勘误、预印本、社论、研究方案和无法取得
  article-version PDF 的记录。
- 资格只依据文章类型、出版状态、文件可用性和原生文字层；不得依据出版社、作者数量、
  页面版式、章节数量、GROBID 输出或 PaperWright 成败换样。
- 保存每个检查位置的纳入/排除理由，以及 PMCID、题名、期刊、DOI、官方检索来源、PDF
  SHA-256、字节数、页数和原生文字字符数。

## 固定运行条件

每篇 PDF 使用同一个 PaperWright 基线和 `--extraction-profile standard` 分别运行两次：

1. **native**：明确不设置 `PAPERWRIGHT_GROBID_URL`；
2. **grobid-crf**：连接同一台 GROBID `0.9.0-crf` 服务，OpenJDK 21，确认 12 个 Wapiti
   模型加载且 `/api/health` 为 ready。

两次运行必须使用不同的空输出目录。保存完整 SourceEvidenceBundle、provider diagnostics、
PaperRecipe/source-element tree、issue routing 和 review index。运行期间不得修改源码、规则、
配置、GROBID 模型或 PDF；失败和 unavailable 按原样计入，不得重跑换样。仅可对明确的瞬时
服务中断重试一次，并同时保存首次错误。

固定 runner 先重验 corpus 和全部 PDF，再检查 GROBID 版本、ready 状态和失败模型数：

```bash
python tools/run_grobid_semantic_eval.py \
  ../paperwright-grobid-semantic-eval-v0.1/CORPUS.json \
  ../paperwright-grobid-semantic-eval-v0.1/runs/baseline-ff8959f \
  --grobid-url http://127.0.0.1:8070 \
  --grobid-version 0.9.0
```

输出目录存在即拒绝覆盖。runner 会明确移除 native 分支的 GROBID/Docling 环境变量，为
两条分支保存 stdout/stderr，生成 `report.json` 和不披露下游采用结果的逐文档 audit task。

为隔离 provider 增量，本轮不调用 Docling、L1、L2 或 L3，不人工修改 ROI、Recipe、claim
或 alignment。

## 机器可计算指标

对每篇论文和每种 GROBID claim type 分别报告原始计数，至少覆盖：

- `title`、`author`、`affiliation`、`abstract`；
- `section`、`paragraph`；
- Figure/Table `caption`；
- `reference`、inline `citation`。

固定指标为：

1. provider `complete/degraded/unavailable` 文档数；
2. claim、coordinate observation 和冲突数量；
3. **native alignment support**：claim 引用的 GROBID observations 中，具有至少一个
   PDFium 原生文字 alignment 的比例；
4. **aligned character coverage**：具有 alignment 的 observation 规范化字符数占全部
   claim observation 字符数的比例；同时报告按最佳 alignment `text_score` 加权的
   **alignment-weighted text coverage**，避免把低文本相似度的几何命中计成完整覆盖；
5. 对照路径新增、移除或改变的 conflicts、specialist requests、Recipe actions 和
   source-element ArticleTree 节点归属；
6. 被下游 decision 实际引用的 GROBID claim 数量。只存在但未被使用的 claim 不算质量
   改善。

分母为零时报告 `not_applicable`，不得写成 0% 或 100%。micro 与 document-macro 结果
分开；不把大量 paragraph/reference claims 淹没少量 title/abstract claims。

## 人工 gold

机器统计完成后，为每篇论文生成只含以下内容的审计任务：PDF 页图、claim type、claim
文本、bbox、对应的 PDFium 原生文字和 alignment 分数；不显示下游是否采用该 claim。

人工对每个 claim 标注：

- `correct`：角色正确，边界覆盖目标语义单元；
- `partial`：角色正确但边界明显缺失或混入相邻单元；
- `wrong_role`：文本存在，但 GROBID 角色错误；
- `unsupported`：无法由 PDF 页面和原生文字支持；
- `uncertain`：页面本身无法稳定裁决。

同时按 PDF 枚举 title、abstract、section heading、Figure/Table caption 和 reference entry
的 gold 单元，用于计算召回。author、affiliation、paragraph 和 inline citation 首轮只报告
claim precision 与边界质量；它们的完整召回需要更细的 span 标注，不能从未出现的 claim
反推。

`correct` 单独计真阳性；`partial` 单列，不并入严格 precision。`uncertain` 保留并从主
分母排除。报告每类型 TP、partial、wrong_role、unsupported、FN、strict precision 和
recall，以及逐文档原始标签。

PMC JATS/XML 可用于定位候选 gold 单元，但最终标签必须回看 article-version PDF；JATS
与 PDF 不一致时以 PDF 为准并记录差异。

## 决策与声明边界

- 本批只有 8 篇，只是工程审计，不能估计所有科研出版物上的泛化准确率。
- 本轮结果不得直接扩大 GROBID 对正文字符的事实权限；PDFium 原生字符继续是正文真值。
- 某一 claim type 只有在没有 `wrong_role/unsupported`、native alignment support 足够高，
  且至少跨 4 篇文档出现时，才可进入“候选确定性采用规则”；否则继续作为冲突提示或局部
  resolver 输入。title/abstract 等低频类型报告逐文档结果，不用少量样本伪造置信度。
- 任何依据本批标签修改的融合规则都会使本批转为开发/校准集。修改后的回放只能证明修复
  已知失败，不能继续称为独立验证；后续需另冻结未见批次。
- “provider 成功运行”“claim 能对齐”“角色判断正确”“最终 Markdown 改善”是四个不同
  结论，报告中不得互相替代。
