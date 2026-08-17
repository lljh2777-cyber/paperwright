# 随机跨页 Caption Holdout v0.3

## 目标

本轮不再按出版社、图密度或显式跨页标记选论文，而是从预先冻结的自然抽样框随机取样，
估计跨页 Figure/Table–caption 关系在自然论文中的出现频率，并检验当前候选路由。全程固定
基线提交 `23c7aad`，不根据本轮结果修改规则。

PDF、完整 evidence、抽样框和逐文档复核材料保存在仓库外：

```text
paperwright-caption-random-holdout-v0.3/
```

## 可重放抽样

抽样框来自 NCBI PMC E-Utilities：

```text
"2025/06/15"[PDAT] AND open access[filter]
```

查询返回完整的 669 个 PMCID。先按数值排序，再使用 seed `0x20260817` 的 xorshift32
Fisher–Yates 排列，依次检查候选，直到冻结 8 篇合格论文。资格仅由元数据和文件属性
决定：同行评议的原始研究、born-digital、具有原生文字层、官方 OA Cloud 有 PDF、未在
既有语料出现。前 12 个候选中排除 1 篇综述、1 篇病例报告、1 篇勘误和 1 篇预印本；
排除时尚未查看跨页标签。

PMC 在 2026 年 8 月移除了旧 FTP/Cloud legacy 文件。本轮先通过 OA Web Service 核验
许可和可用性，再从新的官方 AWS article-version 路径冻结 PDF。抽样框、候选顺序、每个
排除原因、下载 URL、PDF SHA-256 和页数均在仓库外 JSON 中记录。

## 冻结语料

| ID | 期刊 | PMCID | DOI | 页数 |
|---|---|---|---|---:|
| R01 | Public Opinion Quarterly | `PMC12369942` | `10.1093/poq/nfaf021` | 26 |
| R02 | Behavioral Sciences | `PMC12189265` | `10.3390/bs15060821` | 9 |
| R03 | Journal of Personalized Medicine | `PMC12194685` | `10.3390/jpm15060252` | 13 |
| R04 | Pharmaceuticals | `PMC12195666` | `10.3390/ph18060896` | 20 |
| R05 | Journal of Chemical Information and Modeling | `PMC12264937` | `10.1021/acs.jcim.5c00940` | 12 |
| R06 | Molecules | `PMC12196332` | `10.3390/molecules30122595` | 10 |
| R07 | IJID Regions | `PMC12257027` | `10.1016/j.ijregi.2025.100684` | 8 |
| R08 | Cureus | `PMC12261969` | `10.7759/cureus.86051` | 10 |

共 8 篇、108 页、100 个相邻页对、8 个期刊。随机结果中 MDPI 版式占 4/8；没有为了
版式均衡进行事后换样，因此结论只适用于这个明确抽样框，不能外推为所有科研出版物。

## 固定基线结果

`standard` evidence 共产生 58 个局部问题：40 个复杂几何、18 个同页 caption–visual、
0 个跨页 caption–visual。所有页面 contact sheet、显式 next-page 标记搜索和宽松页顶
caption 审计均未发现真实跨页关系，因此当前 silver 裁决为：

| 指标 | 结果 |
|---|---:|
| 相邻页对 | 100 |
| 路由正例 | 0 |
| 银标真实正例 | 0 |
| TP / FP / FN / TN | 0 / 0 / 0 / 100 |
| pair-level observed prevalence | 0% |
| pair-level Clopper–Pearson 双侧 95% CI | 0%–3.62% |

零预测正例使 precision 不可定义，零真实正例使 recall 不可定义。TN=100 只能说明这批
负例上没有误报，不能写成“准确率 100%”或“路由已经通过总体泛化验收”。

## 当前边界与下一步

- 本轮未改规则，也未把结果回填到实现，因此在人工签署前仍保持独立 holdout 身份；
- 当前标签是 AI 页面证据复核得到的 `silver`，仓库外 `HUMAN_REVIEW.md` 将 100 个页对
  压缩成 8 张全页 contact sheet 供人工逐文档确认；
- 人工确认后才能固定为 gold negative holdout；
- 即使转为 gold，本批仍无法估计 precision/recall。下一轮应继续使用已冻结的候选顺序，
  在不调参前提下扩大固定样本量，而不是再次按正例标记挑选论文。

官方数据边界参见 [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/)
和 [PMC AWS Cloud Service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)。
