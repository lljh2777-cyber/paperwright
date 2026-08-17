# 随机跨页 Caption Holdout v0.5

## 当前状态

- 事前协议提交：`9eb1ade`
- 固定代码基线：`e94cf33218e4df5a7ff8cb920d7e68fb271b2fc9`
- 语料状态：资格、PDF、baseline evidence、silver 与 gold 已冻结
- 标签状态：gold；Liao Li 于 2026-08-17 完成全部 12 篇人工复核
- 规则状态：gold 签署前后均未修改

本轮用于独立验证 v0.4 gold 后开发的 `cross_page_panel_continuity` 修正。完整停止规则、
资格条件、主次指标和解释边界见
[RANDOM_HOLDOUT_V0.5_PROTOCOL](RANDOM_HOLDOUT_V0.5_PROTOCOL.md)。PDF、抽样框、资格记录和
完整 evidence 保存在仓库外：

```text
paperwright-caption-random-holdout-v0.5/
```

## 资格冻结

按冻结顺序检查候选位置 38–54，共 17 篇；纳入 12 篇、排除 5 篇。排除项为 2 篇病例
报告、1 篇综述、1 篇临床图像短文和 1 篇单病例手术报告。资格阶段只查看文章类型、OA
文件可用性、页数和原生文字量，没有运行 PaperWright、生成页面预览或检查跨页标签。

| ID | 候选位 | 期刊 | PMCID | DOI | 页数 |
|---|---:|---|---|---|---:|
| H01 | 38 | Molecules | `PMC12195824` | `10.3390/molecules30122597` | 15 |
| H02 | 40 | Molecular Pharmaceutics | `PMC12776576` | `10.1021/acs.molpharmaceut.5c00170` | 15 |
| H03 | 41 | Molecules | `PMC12196148` | `10.3390/molecules30122594` | 21 |
| H04 | 42 | FASEB Journal | `PMC12139580` | `10.1096/fj.202403196R` | 13 |
| H05 | 44 | Journal of the American Chemical Society | `PMC12232316` | `10.1021/jacs.5c03056` | 8 |
| H06 | 47 | World Journal of Gastrointestinal Oncology | `PMC12179956` | `10.4251/wjgo.v17.i6.106154` | 9 |
| H07 | 48 | Sensors | `PMC12197058` | `10.3390/s25123746` | 16 |
| H08 | 49 | Cureus | `PMC12263343` | `10.7759/cureus.86087` | 8 |
| H09 | 50 | Cancer Control | `PMC12174713` | `10.1177/10732748251351423` | 9 |
| H10 | 52 | Biomedicines | `PMC12191203` | `10.3390/biomedicines13061477` | 17 |
| H11 | 53 | Sensors | `PMC12197104` | `10.3390/s25123750` | 18 |
| H12 | 54 | FASEB Journal | `PMC12147993` | `10.1096/fj.202500954R` | 9 |

共 12 篇、158 页、146 个相邻页对、9 个期刊。外部冻结记录的 SHA-256：

- `sampling-frame.json`：`3b21d4427cba8f5858e178d6780372c8e63f44a995e35a7518f6009d2bdfd82a`
- `eligibility.json`：`f1236e00d855b5ba06b7d62f630a63b209d7472c6fe0002c2333b555454afa2e`
- `corpus.json`：`7c7def68117bf9abb8a1c8606870a1fd137db17591084ae64ac3a8887459197e`
- `PREREGISTRATION.md`：`571671d5555d75c7ef8e5cce391e94ea63dab5c4a45373d124f579f9955b588f`
- `pair-audit-silver-v0.5.json`：`95a53aed13307183f8a550f4d6413b4b55ffce0babbd03a9a6f7754b22fca865`
- `HUMAN_REVIEW.md`：`1cce784ca7934e64582a1120f3a18cee26b39a1d7c1546cbc57989f6b4660b35`
- `pair-audit-gold-v0.5.json`：`2a5203f1b6ca253020bab38635eb6131b8494fc2e98cf480967fbd04060230b0`
- `results.json`：`c4c2fb42cfadf568b1b8913087ae523f955d6381c6a70cd79227cec467e83a8e`
- `l2-evaluation-qwen3.7-plus/results-l2.json`：
  `0751b8a6d97badc68fb1345821a8d8e151fdc8c01f87d9d4c9c79ba14059ac11`

## 固定基线与 human-verified gold 结果

严格使用固定基线和 `--extraction-profile standard` 生成了 105 个 issue：52 个复杂
几何、47 个同页 caption–visual 和 6 个跨页 caption–visual。独立于路由输出的 12 张
全页 contact sheet 审计发现 4 个 silver 正例，分布于 2 篇论文；无 uncertain。Liao Li
随后逐篇复核全部 contact sheet，人工与 silver 在文档二分类、精确评分页对集合以及全部
相邻页标签上分别为 12/12、12/12 和 146/146 一致（Cohen's kappa = 1.0），因此将本批
提升为 gold。

H03 的人工原始记录包含 `7-9`，reviewer 澄清其含义是同一 Figure 连续跨越第 7、8、9
页。对象级 span 被完整保留；按事前的相邻页评分契约，只有完整显式 caption 首次出现前
的 `8-9` 计为正例，`7-8` 因第 8 页只有中间 continuation marker 而不计正例。

| 指标 | gold 结果 |
|---|---:|
| TP / FP / FN / TN | 4 / 2 / 0 / 140 |
| 候选 precision | 66.67% |
| 候选 recall | 100% |
| gold 负页对候选误报率 | 1.41% |
| pair observed prevalence | 2.74% |
| 含正例文档 | 2/12（16.67%） |

4 个 gold 正例均是“前页视觉 + 后页继续面板并首次出现完整 legend”的多页面板 Figure，
说明新增连续性信号在本批没有漏召回。两个误报分别是：把中间页仅有的
`Figure 3. Cont.` 标记当成完整 legend 起点；把前页 Figure 6 与后页独立 Figure 7
误关联。观察点估计达到事前的 recall/FPR 工程目标，但正例数少于预设的 5 个，因此必须
按协议视为**独立但探索性**的结果，不能宣称泛化验收通过。

外部 `HUMAN_REVIEW.md` 没有显示确切 silver 页对；但 reviewer 已在进度更新中得知聚合
结果、候选文档族和误报类型，所以本次必须称为 **silver-informed review**，不能称为
盲审。gold 签署前后均未修改任何路由规则或阈值，v0.5 也尚未用于调参。

## 固定 L2 次指标

确定性候选路由仍是本轮预注册主指标。gold 冻结后，使用固定的 `qwen3.7-plus`、
temperature 0 和 `paperwright-cross-page-caption-prompt-v0.2` 对全部 6 个路由候选进行
一次模型层 relation review；gold 标签没有进入 prompt。结果与路由指标分开：

| 范围 | TP / FP / FN / TN | precision | recall |
|---|---:|---:|---:|
| 6 个候选内 | 4 / 1 / 0 / 1 | 80% | 100% |
| 全部 146 个相邻页对的端到端结果 | 4 / 1 / 0 / 141 | 80% | 100% |

模型正确拒绝 H10 的 6–7：前页 Figure 6 与后页 Figure 7 是不同对象；但接受了 H03 的
7–8，把只有 `Figure 3. Cont.` 的中间页错误视为完整 caption 起点。L2 因而消除了路由
两个误报中的一个，没有新增漏报；这只是一个固定模型 alias 的单次、极小样本结果，不
估计模型跨运行或未来版本的稳定性。

原计划先让视觉布局桥只处理 11 个候选相关页面，但 H01、H03、H10 的首个页面均在三次
重试后被不可变布局契约拒绝：两篇没有完整核算候选，一篇产生 ROI 越界区块，0 个
`final-layout.json` 写盘。为避免把页面布局 JSON 合规性混入 paired-page 语义指标，最终
task 透明地从冻结的 6 个 issue-routing 页对、bbox 和原生 caption anchor 构造，并继续
通过正式 task/review 校验器。该 adapter 限制和最初失败均保存在外部结果中。

供应商原始 usage 仅作为可复现 provenance 保存，不计算价格、不设置预算阈值，也不纳入
质量结论。本轮 3 次有效 paired-page 调用的记录为 14,839 input、5,597 output，其中
5,121 reasoning tokens；失败的布局尝试没有生成 usage 文件，因此不做不完整汇总。
