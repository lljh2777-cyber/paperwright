# 随机跨页 Caption Holdout v0.4

## 状态

- 事前协议提交：`31ca9c7`
- 固定代码基线：`6f322ecb4debb8df086fd1414e711f7104ca5118`
- 当前标签层级：silver，等待 silver 具体位置遮蔽的人工复核
- 规则状态：冻结后未修改

本轮继续使用 v0.3 已冻结的 669 篇 PMC OA 抽样框和候选顺序，从位置 13 开始，按事前
规定走到 12 篇合格论文后停止。停止规则、资格条件、评估流程和统计边界在查看候选页面前
写入 [RANDOM_HOLDOUT_V0.4_PROTOCOL](RANDOM_HOLDOUT_V0.4_PROTOCOL.md) 并推送。

PDF、完整 evidence、抽样/资格记录、contact sheet 和盲审表保存在仓库外：

```text
paperwright-caption-random-holdout-v0.4/
```

## 资格冻结

候选位置 13–37 共检查 25 篇，纳入 12 篇、排除 13 篇。排除项为 4 篇综述/系统综述、
3 篇病例报告、3 篇预印本、1 篇 letter、1 篇 erratum，资格判断没有使用页面版式、
Figure 数量或跨页标签。全部纳入 PDF 来自官方 PMC OA article-version 路径，均为
born-digital 且具有原生文字层；文件大小、页数、文字字符数和 SHA-256 已在运行基线前
写入外部 `corpus.json`。

| ID | 候选位 | 期刊 | PMCID | DOI | 页数 |
|---|---:|---|---|---|---:|
| V01 | 15 | World Journal of Gastrointestinal Oncology | `PMC12179933` | `10.4251/wjgo.v17.i6.106080` | 19 |
| V02 | 16 | Journal of Experimental Orthopaedics | `PMC12167628` | `10.1002/jeo2.70310` | 8 |
| V03 | 17 | Plants | `PMC12197099` | `10.3390/plants14121836` | 15 |
| V04 | 21 | Polymers | `PMC12197251` | `10.3390/polym17121659` | 18 |
| V05 | 23 | CNS Drugs | `PMC12263469` | `10.1007/s40263-025-01194-4` | 11 |
| V06 | 24 | Veterinary Research Forum | `PMC12295531` | `10.30466/vrf.2024.2029765.4289` | 8 |
| V07 | 26 | Discover Oncology | `PMC12167730` | `10.1007/s12672-025-02956-8` | 16 |
| V08 | 27 | Medical Education | `PMC12513550` | `10.1111/medu.15748` | 13 |
| V09 | 29 | Malaria Journal | `PMC12168317` | `10.1186/s12936-025-05446-y` | 12 |
| V10 | 31 | World Journal of Diabetes | `PMC12179895` | `10.4239/wjd.v16.i6.105173` | 22 |
| V11 | 32 | World Journal of Diabetes | `PMC12179872` | `10.4239/wjd.v16.i6.102727` | 17 |
| V12 | 37 | Plant Biotechnology Journal | `PMC12392971` | `10.1111/pbi.70205` | 10 |

本批共 12 篇、169 页、157 个相邻页对、11 个期刊。与 v0.3 合计为 20 篇、277 页、
257 个相邻页对。

## 固定基线与 silver 结果

`layout-prepare --extraction-profile standard` 共产生 106 个 issue：60 个复杂几何、
41 个同页 caption–visual、5 个整页视觉保留、**0 个跨页 caption–visual**。

不依赖路由候选的全页 contact sheet 审计发现 9 个 silver 正例，分布于 4 篇论文：

| 文档 | 一基页对 | 共同结构 |
|---|---|---|
| V01 | 8–9、10–11、13–14 | 多面板 Figure 跨页，caption 在后一页 |
| V04 | 12–13 | 多面板 Figure 跨页，caption 在后一页 |
| V10 | 8–9、11–12、13–14、17–18 | 多面板 Figure 跨页，caption 在后一页 |
| V11 | 10–11 | 多面板 Figure 跨页，caption 在后一页 |

这些正例的后一页同时含有同一 Figure 的后续面板。现有规则把“caption 页已有本地同类
视觉”视为跨页绑定的反证，因此 9 个正例均未进入路由。这是同一可解释失败族，而不是
9 个互不相关的阈值偏差。

| 指标 | silver 结果 |
|---|---:|
| TP / FP / FN / TN | 0 / 0 / 9 / 148 |
| precision | 不可定义（无预测正例） |
| recall | 0% |
| specificity | 100% |
| pair observed prevalence | 5.73% |
| pair Clopper–Pearson 双侧 95% CI | 2.65%–10.60% |
| document-cluster bootstrap pair prevalence 95% CI | 0.78%–10.40% |
| 含正例文档 | 4/12（33.33%） |

与 v0.3 gold 合并仅作描述时，累计观察为 9/257 个相邻页对（3.50%，exact 95% CI
1.61%–6.54%）和 4/20 篇论文（20%）。相邻页对在文档内相关，因此同时报告了固定 seed、
100,000 次按文档重采样的 percentile bootstrap；累计 pair prevalence 的 cluster 区间为
0.42%–7.04%。

## 当前解释与下一步

这是独立 holdout 上的 silver 漏召回证据，说明 v0.3 的 100 个全负页对不足以验证规则。
当前不能直接改“本页视觉优先”规则，否则会把尚未人工签署的 holdout 用作调参集。

下一步是由人工只看 12 张全页 contact sheet，填写仓库外 `HUMAN_REVIEW.md`。人工已经
知道聚合 silver 正例数，因此这不是完全盲审；表中故意隐藏具体文档和页对答案。若人工
确认失败族：

1. 先生成 human-verified gold 和冻结基线混淆矩阵；
2. 再把 v0.4 明确转为开发/校准集，设计“跨页面板连续性”证据，而不是删除本地视觉抑制；
3. 保持旧困难负例回归，并从候选位置 38 继续建立新的、未见标签 holdout 验证修正。

官方数据边界参见 [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/)
和 [PMC AWS Cloud Service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)。
