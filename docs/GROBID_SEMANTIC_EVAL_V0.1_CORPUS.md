# GROBID 科研语义证据评估 v0.1：冻结语料

冻结时间：2026-08-22 09:55:18 +08:00

事前协议：[`GROBID_SEMANTIC_EVAL_V0.1_PROTOCOL.md`](GROBID_SEMANTIC_EVAL_V0.1_PROTOCOL.md)

## 冻结结果

沿既有 PMC OA 669 篇随机候选顺序从位置 65 开始检查。位置 65–72 的前 8 篇均满足
“同行评议原始研究、官方 OA PDF、born-digital、原生文字层可用、未进入既有语料”的
预注册条件，因此按停止规则在位置 72 截止，没有按版式、期刊或预期 GROBID 难度换样。

冻结时没有运行 PaperWright 或 GROBID，没有生成页面预览，也没有检查 front matter、
章节、caption 或参考文献的语义标签。

| ID | 位置 | PMCID | 期刊 | 页数 | 原生文字字符 |
|---|---:|---|---|---:|---:|
| g01-pan-ras-darpin | 65 | [PMC12591329](https://pmc.ncbi.nlm.nih.gov/articles/PMC12591329/) | Molecular Oncology | 21 | 87,851 |
| g02-cerebral-palsy-detection | 66 | [PMC12618952](https://pmc.ncbi.nlm.nih.gov/articles/PMC12618952/) | Developmental Medicine & Child Neurology | 10 | 49,667 |
| g03-barley-suberization | 67 | [PMC12167721](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167721/) | Planta | 18 | 76,824 |
| g04-hes6-pan-cancer | 68 | [PMC12167741](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167741/) | Discover Oncology | 42 | 60,025 |
| g05-safe-adaptive-control | 69 | [PMC12321936](https://pmc.ncbi.nlm.nih.gov/articles/PMC12321936/) | Nonlinear Dynamics | 27 | 88,578 |
| g06-athlete-mental-health | 70 | [PMC12200347](https://pmc.ncbi.nlm.nih.gov/articles/PMC12200347/) | South African Journal of Sports Medicine | 8 | 39,092 |
| g07-diabetic-sudden-deafness | 71 | [PMC12179903](https://pmc.ncbi.nlm.nih.gov/articles/PMC12179903/) | World Journal of Diabetes | 10 | 32,503 |
| g08-rice-growth-fungi | 72 | [PMC12196539](https://pmc.ncbi.nlm.nih.gov/articles/PMC12196539/) | Plants | 15 | 54,409 |

总计 8 篇、151 页、488,949 个 PDFium 原生文字字符、36,031,581 bytes。8 个 PDF 均以
`%PDF-` 开头、可由 pypdfium2 完整打开，其 SHA-256 与既有 PaperWright evaluation corpus
中的 PDF 无重复。

## 来源与不可变边界

NCBI OA API 为每篇返回官方 OA package 记录，但返回的 FTP package 在冻结时为 404；
因此全部使用同一 PMC OA Open Data 官方 S3 镜像的 `{PMCID}.1.pdf`，并在清单中同时保存
OA API package URL、实际 retrieval URL 和 `retrieval_fallback=true`。

本地冻结根目录为工作区同级的 `paperwright-grobid-semantic-eval-v0.1/`：

- `CORPUS.json` SHA-256：
  `c3bd2a9c669fc83f1a8d39b4b43ba2f765c344070a6bf6833e23c2791ed435e6`；
- `ELIGIBILITY.json` SHA-256：
  `658ca9cac2b1b08620c122900cdf8406b13a3da912e10cd8d683f855d22655e6`；
- sampling frame SHA-256：
  `3b21d4427cba8f5858e178d6780372c8e63f44a995e35a7518f6009d2bdfd82a`。

后续运行必须先重算全部 PDF 与清单哈希，输出到新的拒绝覆盖目录。发现哈希变化时整批停止，
不得用重新下载的论文继续冒充同一冻结语料。

## 下一步

确定性 E7 runner 已实现为 `tools/run_grobid_semantic_eval.py`：它会验证 corpus，分别在
明确移除 GROBID 环境变量和连接固定 CRF 服务的条件下执行
`layout-prepare --extraction-profile standard`，保存成对证据包，再生成逐 claim type 的
机器统计和不暴露下游采用结果的人工审计任务。下一步是在冻结输出目录执行完整批次。
