# 跨页 Figure/Table–caption 关系 v0.1

## 目标

处理科研论文中“视觉对象位于第 N 页底部，而其显式 caption 位于第 N+1 页顶部”的
关系。旧实现只能在 `layout-apply` 后用几何分数猜测，模型既看不到成对页面，也不能
明确拒绝错误绑定。v0.1 把它提升为可路由、可审查、可拒绝和可回放的关系契约。

## 两阶段关系发现

### 1. Evidence/route 阶段

`issue-routing.json` 新增 `cross_page_caption_visual_binding`。issue 以 caption 页为
`page_index`，并通过 `scope.related_page_indices` 引用前一页。兼容执行器会把两页都
加入 L2，而不是只让模型看到 caption 页。

触发条件保持保守：

- 第 N+1 页存在行首显式 Figure caption；普通前页视觉证据只触发顶部 caption，前页
  视觉占主导或有显式方向标记时才接受页中/页底 caption；
- 第 N 页存在靠近页底、面积足够的视觉候选，或带“caption/legend on next page”显式
  标记，或页面由极少原生文本与非空 raster evidence 构成；
- 后页的 `◀` / “continued from previous page”标记可作为方向证据；
- 后页已有同类型本地视觉对象时默认不生成跨页关系候选；显式方向标记或满足全部
  `cross_page_panel_continuity` 结构证据时才覆盖；
- 只考虑相邻页，不扫描任意远距离页面。

### 2. FinalLayout 后关系审查

页面布局完成后生成：

- `cross-page-caption-task.json`
- `cross-page-caption-review.json`
- `cross-page-caption-usage.json`（供应商原始 usage，不含价格或预算）

任务只给出稳定的 `pNNNN:region-id`、两页 bbox、原生 caption 文本和已有角色。模型
必须对每个 caption 做且只做一种选择：绑定一个列出的 previous-page visual，或明确
reject。模型不得绘制 bbox、转写正文、创建候选或修改页面布局。

```bash
PYTHONPATH=src python tools/run_cross_page_caption_review.py layout-review --dry-run
PYTHONPATH=src python tools/run_cross_page_caption_review.py layout-review
```

`tools/run_routing_plan.py` 已自动在所有 `final-layout.json` 校验后、`layout-apply` 前
执行该桥。没有候选时写零调用的空 review，不加载模型 SDK。

## 确定性验收与投影

校验器强制：

- task 与每页 materialized FinalLayout 哈希绑定；
- 每个 caption 必须被 binding 或 rejection 完整核算；
- visual/caption ref 必须来自 task，且 visual 只能使用一次；
- visual 必须位于相邻前一页，Figure/Table 类型必须匹配；
- review 中不允许文本、bbox 或任意新 ref。

接受的关系以 `reviewed_cross_page_relation` 覆盖旧几何猜测；显式 rejection 会阻止旧
heuristic 再次偷偷绑定。最终关系进入 image record、ArticleModel、Reader asset 的
`caption_block_id` 以及 `caption-of` relation，保留两个页面各自的 source span。

## v0.1 校准与限制

- 自生成跨页正例已覆盖：任务生成、非法 ref 拒绝、显式接受、显式拒绝、writer、
  ArticleModel 和 Reader 投影；
- 建立了 9 篇论文、16 个相邻页样本的真实 `silver` seed set：10 个正例与 6 个困难
  负例；正例覆盖顶部/底部 caption、独立标签和显式续页标记；
- 用新鲜 standard evidence 对该 seed set 回放，当前规则得到 TP=10、FP=0、FN=0、
  TN=6。该集合参与了规则修正，因此这是 calibration 结果，不是独立泛化指标；
- *Attention Is All You Need* 15 页产生 0 个跨页 issue，原有 15 个局部 L2 issue
  保持不变；
- 当前仅覆盖“前页视觉 → 后页顶部 caption”，不覆盖跨两页以上、caption 本身跨页、
  或视觉对象在后一页而 caption 在前一页的情形；
- 首个独立出版社 holdout 在 `cf71d02` 上冻结后发现 17 个跨页假阳性；结构规则修正后
  这批样本降为 0，旧 seed set 仍为 TP=10、FP=0、FN=0、TN=6。详见
  [独立出版社 Holdout v0.1](HOLDOUT_V0.1.md)；
- holdout 未包含真实跨页正例，且修正规则已经看过这些失败，因此它现在只能作为回归集。
  后续 marker-selected 挑战集补入 7 个出版社显式正例，当前规则全部召回，并用 1 个
  `Figure 1A` 裸面板标签负例补强锚点过滤；详见
  [跨页 Caption 挑战集 v0.2](CAPTION_CHALLENGE_V0.2.md)；
- 挑战集 8 例已于 2026-08-17 由 Liao Li 全量复核，标签 8/8 一致并形成 gold；但它按
  显式正例标记富集且已经参与修正，不能把结果描述成模型质量、自然 prevalence 或总体
  泛化验收；
- 随机 holdout v0.3 通过固定 PMC OA 抽样框和随机种子冻结 8 篇、100 个相邻页对，当前
  路由正例和真实正例均为 0；100 个负例随后全部完成人工签署并形成 gold。该结果只能
  给出 pair-level prevalence 的 0%–3.62% 双侧 95% 区间，precision/recall 均不可定义；详见
  [RANDOM_HOLDOUT_V0.3](RANDOM_HOLDOUT_V0.3.md)。
- 事前注册的随机 holdout v0.4 沿同一候选顺序新增 12 篇、157 个相邻页对。固定基线
  预测 0 个跨页候选；人工 gold 最终确认 10 个正例，全部是“上一页含早期面板、caption
  页含后续面板及显式 caption”的多页 Figure。它暴露出“本页已有同类视觉就抑制跨页”的
  系统漏召回；gold 签署前未修改规则，冻结基线 TP=0、FP=0、FN=10、TN=147，详见
  [RANDOM_HOLDOUT_V0.4](RANDOM_HOLDOUT_V0.4.md)。
- v0.4 在 gold 后已明确转为开发/校准集。新增 `cross_page_panel_continuity` 信号要求
  前页大型 raster Figure 延伸到页底、后页页顶视觉片段紧邻 caption，并证明前页视觉
  未被本页 caption 终止；`Figure N. Cont.` 和前一 caption 后的新视觉是显式覆盖证据。
  低非文字残余覆盖会排除整页装饰/侧栏。v0.4 校准回放 TP=10、FP=0、FN=0、TN=147，
  v0.3 的 100 个 gold 负页对和 challenge 的困难负例均保持不变。paired-page prompt
  使用 v0.2，提醒模型 caption 页的本地面板可能是同一 Figure 的后续部分。
