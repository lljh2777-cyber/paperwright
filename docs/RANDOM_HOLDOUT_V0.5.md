# 随机跨页 Caption Holdout v0.5

## 当前状态

- 事前协议提交：`9eb1ade`
- 固定代码基线：`e94cf33218e4df5a7ff8cb920d7e68fb271b2fc9`
- 语料状态：资格与 PDF 已冻结
- 标签状态：未查看
- 规则状态：冻结后未修改

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

## 下一步

严格使用固定基线和 `--extraction-profile standard` 生成 evidence。只有 12 篇全部运行
完成并保存路由输出后，才生成不依赖候选的全页 contact sheet 和相邻页 silver 审计。
