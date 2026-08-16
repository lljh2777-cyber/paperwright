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

- 第 N+1 页顶部 30% 内存在显式 Figure caption；
- 第 N 页存在靠近页底、面积足够且有 image/vector/raster/drawing 证据的视觉候选；
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
- 对现有 16 份 review bundle、305 页做离线扫描，没有产生跨页 issue；这说明当前
  阈值在该语料上没有新增误报，但语料中也没有经确认的跨页正例；
- *Attention Is All You Need* 15 页产生 0 个跨页 issue，原有 15 个局部 L2 issue
  保持不变；
- 当前仅覆盖“前页视觉 → 后页顶部 caption”，不覆盖跨两页以上、caption 本身跨页、
  或视觉对象在后一页而 caption 在前一页的情形；
- 尚未用外部视觉模型完成正例语义校准，因此不能把契约通过描述成模型质量验收。
