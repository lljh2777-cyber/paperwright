# PaperWright 真实科研论文质量基线 v0.1

> 日期：2026-08-16  
> 语料：16 篇 born-digital 科研论文，共 305 页  
> 审阅：3 个 `gpt-5.6-luna/high` 子代理，按统一协议分批独立检查

## 1. 本轮评测的边界

本轮用于回答两个问题：当前确定性输出在哪些真实科研版式上失败，以及现有路由能否
把这些问题送到合适的处理层。

执行内容包括：

- 以 `--furniture auto --region-render-mode auto` 完成 16/16 篇规则基线转换；
- 对全部 305 页生成 `layout-prepare` 证据与 `routing.json`；
- 分层视觉检查 126 个去重页面，覆盖首页、正文页、复杂图表页和末尾/参考文献页；
- 对照源页面、`article.md`、`manifest.json`、`physical_document.json` 和路由计划；
- 使用 `docs/EVALUATION_PROTOCOL_V0.1.md` 的八个维度记录 64 个具体 issue。

本轮没有执行 DashScope L1/L2 bridge，因此结论是“当前规则输出基线 + 路由审计”，
不是某个外部模型的最终端到端质量。子代理只承担评测者角色，没有改写论文正文。

## 2. 总体结果

| 项目 | 结果 |
|---|---:|
| 流水线完成 | 16/16 |
| `success_with_degradation` | 16/16 |
| overall major | 13/16 |
| overall minor | 3/16 |
| 无保留 pass | 0/16 |
| 抽样页面 | 126 |
| issue | 64 |
| major issue | 42 |
| minor issue | 22 |
| 无来源生成/实质改写 | 0 |

“16/16 转换成功”只说明程序完整运行，不代表重建质量合格。真实页面审阅显示，目前
manifest 的成功状态与读者可接受质量之间仍有明显差距。

### 2.1 文档级质量维度

| 维度 | pass | minor | major |
|---|---:|---:|---:|
| text integrity | 0 | 12 | 4 |
| reading order | 3 | 8 | 5 |
| section structure | 9 | 5 | 2 |
| visual completeness | 4 | 4 | 8 |
| caption binding | 4 | 4 | 8 |
| furniture exclusion | 9 | 6 | 1 |
| provenance | 12 | 2 | 2 |
| uncertainty handling | 1 | 11 | 4 |

当前最可靠的是来源追踪和基础章节结构；最薄弱的是视觉完整性、caption 绑定、文字
细节完整性和不确定性处理。

## 3. 路由审计

| Route | 页数 | 比例 |
|---|---:|---:|
| L0_RULE | 14 | 4.6% |
| L1_TEXT_MODEL | 231 | 75.7% |
| L2_VISUAL_MODEL | 49 | 16.1% |
| HUMAN_REVIEW | 11 | 3.6% |
| L3_PROGRAM_SYNTHESIS | 0 | 0.0% |

`lowercase_continuation_fragments` 把 231 页送入 L1。真实论文中的行碎片、公式标签、
作者信息和图内文字会频繁触发该信号，导致路由不是预期的稀疏升级。

与此同时，多篇论文的矢量密集 Figure、三栏页面、跨页 Figure-caption 和整页图仍落入
L1 或 L0。这说明当前问题不只是阈值过高或过低，而是页级单标签无法表达“一页正文
可按规则处理，但其中一个图和 caption 必须视觉确认”的现实结构。

## 4. 主要失败簇

### P0：视觉内容静默缺失

20 个 issue 属于 `visual_completeness`，聚合建议中 `render_visual` 出现 25 次。典型
情况是大型矢量图只留下标签文字、小位图碎片或 caption，完整 Figure 没有进入输出。

另有无原生文字层页面被正确路由到 HUMAN_REVIEW，但规则基线最终只写空 page marker，
没有保留整页图，也没有在阅读输出中留下显式 unresolved 状态。

### P0：caption 与视觉对象关系不可靠

12 个 issue 属于 `caption_binding`。常见错误包括 caption 先于图像、跨页 caption 未与
上一页 Figure 绑定、多面板图被拆散后仍保留孤立 caption，以及缺失 Figure 的 caption
被当作普通正文。

### P1：多栏与跨页阅读顺序

三栏正文、双栏参考文献、侧置 caption 和跨页 Figure-caption 仍可能串栏或错序。纯文本
模型可以判断已有块关系，但前提是 evidence 层先提供正确的列、对象和页间候选。

### P1：路由过度升级与错误升级并存

L1 覆盖 75.7% 页面，但仍漏掉真正需要视觉判断的页面。这验证了 Hybrid v1 应从
“整页选择 L0/L1/L2”迁移到“先确定性处理整页，再为具体 unresolved issue 选择处理器”。

## 5. 失败归因

| 最早可阻止错误的层 | Issue 数 |
|---|---:|
| routing | 17 |
| evidence | 13 |
| rule | 13 |
| extraction | 10 |
| projection | 6 |
| validation | 5 |

这组结果不支持立即优先扩展 PaperRecipe。缺少或不完整的视觉证据无法靠更复杂的程序
操作恢复；应先保证 evidence 和 validation 不允许页面或主要视觉对象静默消失。

## 6. 下一项工程：Completeness Gate v0.1

下一项工作应实现“页面/视觉完整性门禁 + 确定性视觉兜底”，验收条件为：

1. 每个源页面最终至少具有可追溯正文、视觉资产或显式 unresolved/human-required 记录；
2. `native_text_missing` 页面不得静默输出空 page marker；无法结构化时至少保留整页图；
3. 大面积、矢量密集或 Figure-caption 明确但没有完整资产的区域生成 ROI/page render
   候选；
4. caption 存在但没有可绑定视觉对象时，质量校验不得返回无条件成功；
5. 完整性判断写入结构化报告，并能定位到 page、candidate/element 和触发原因；
6. 用本轮发现的页面建立不含论文正文的小型回归清单，验证不再静默漏页、漏图。

完成该门禁后，再实施 issue-level routing；其后才是跨页 caption binding 和 PaperRecipe
操作集扩展。

### 6.1 实施结果（2026-08-16）

Completeness Gate v0.1 已接入 direct 与 hybrid writer：

- 每页写入 `accepted` / `suspicious` / `human_required` / `invalid` 状态；
- 无文字、非空且尚无视觉投影的页面自动进行确定性整页渲染；
- 孤立 caption 和“矢量密集 + 明确 caption + 无视觉资产”不再无条件成功；
- `_paperwright/completeness-report.json` 与 manifest SHA-256 清单形成哈希绑定；
- hybrid 在原生文字未投影时阻断 Article Model 编译。

A06 真实回归中，此前为空的第 31–32 页均生成了完整 fallback PNG；报告同时将第 4、
8、9 页等缺少视觉对象的 caption/vector 风险标为 `suspicious`。详细契约与边界见
[COMPLETENESS_GATE_V0.1](COMPLETENESS_GATE_V0.1.md)。

### 6.2 Issue-level Routing 实施结果（2026-08-16）

`issue-routing.json` 已替代页级单标签成为主路由语义：每页固定先走 L0，升级只属于
带 bbox/element/candidate/block 证据的具体问题。A06 standard 回归从旧页级方案的广泛
L1 猜测收敛为 10 个问题：第 3、4、5、8、9、10 页的 Figure-caption 绑定，第 28 页的
复杂几何，以及第 30–32 页的确定性整页视觉保留。布局前不再猜测段落续接；ArticleModel
形成后才从验证器认可的相邻 block pair 生成 L1 issue。详见
[ISSUE_ROUTING_V0.1](ISSUE_ROUTING_V0.1.md)。

## 7. 可复现产物

真实 PDF、页面图像、完整转换结果和逐篇标注遵循 `docs/STORAGE_POLICY.md`，保存在
仓库外。聚合工具为 `tools/summarize_quality_baseline.py`，它校验论文 SHA-256、页数、
八维结果和 issue 字段，并拒绝覆盖已有报告。

仓库外本轮目录包含：

```text
paperwright-evaluation-v0.1/
├── current-direct/
├── layout-review/
├── annotations/
├── agent-reports/
└── aggregate-final/
    ├── corpus.json
    ├── baseline-summary.json
    ├── failure-taxonomy.md
    └── baseline-report.md
```

本轮只记录模型名称、审阅事实和产物，不计算价格，也不实施 token 预算限制。
