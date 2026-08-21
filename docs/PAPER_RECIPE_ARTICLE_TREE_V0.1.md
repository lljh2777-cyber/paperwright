# PaperRecipe 与 ArticleTree v0.1

> 状态：已实现的纵向原型（2026-08-21）

## 目的

E0–E4 解决“有哪些原生对象、不同 provider 如何观察、哪里发生冲突”。E5 增加一个
论文级决策边界：结构判断可以改变分类、排除、顺序、绑定或渲染方式，但不能生成、替换
或删除原生正文事实。

## 产物

`paper-recipe.json` 使用 `paperwright-paper-recipe-v0.1`：

- 绑定 source PDF、PhysicalDocument 和 SourceEvidence index 的 SHA-256；
- 动作集合限定为 `classify/exclude/split/merge/order/bind/render`；
- 每个动作引用 page、原生 element ID 或 bbox、evidence ref 和理由；
- 明确禁止文件、网络、随机数、正文替换和 Markdown 写入能力；
- 保存确定性 action trace 哈希和 `ready/degraded/human_required` 状态。

prepare 目录的 `article-tree.json` 使用 `paperwright-article-tree-v0.1`；发布到标准/完整
证据包时命名为 `_paperwright/02-structure/source-element-tree.json`：

- document → page → source-element 三层结构；
- 每个 PhysicalDocument 元素恰好进入一个叶节点；
- 叶节点保存 source text SHA-256，不复制或改写正文；
- disposition 只能为 `keep/render/exclude`；
- summary 必须满足三类元素计数之和等于物理元素总数，且
  `generated_text_count` 恒为 0。

v0.1 的 producer 是确定性 baseline，不调用模型。未来 AI producer 必须输出同一契约并
通过同一验证器，不会获得新的正文权限。

## prepare/apply 边界

`layout-prepare` 在 SourceEvidence 写完并通过验证后生成 Recipe 和 ArticleTree，把路径、
版本、状态与哈希写入 `review-index.json`。`layout-apply` 拒绝缺失配对、路径越界、哈希
漂移、输入身份变化或确定性重放不一致；standard/full 包把两份产物复制到
`_paperwright/02-structure/` 并列入 manifest。

apply 随后编译 `paperwright-article-tree-v0.2`，保存到
`_paperwright/article-tree.json` 并作为 ArticleModel 的唯一上游；详见
[Final ArticleTree v0.2](FINAL_ARTICLE_TREE_V0.2.md)。当前安全投影只实现以下窄动作：

1. provider Table bbox 作为图片渲染，并从 text region 移除同一原生元素；
2. 未被布局覆盖的原生 image 作为 Figure 保留；
3. caption 必须由原生 `Figure/Table N` 锚或 provider semantic claim 支撑；
4. 首页小型 raster-only 出版标记，以及首页底部小型 vector 出版家具可排除。

其它动作虽然在能力白名单中保留，但 v0.1 baseline 尚不主动产生 split/merge/order/bind。

## 已知边界

- v0.1 ArticleTree 仍只负责审计和安全决策；最终文章结构由 v0.2 表达，两者通过规范
  SHA-256 绑定，职责不混合。
- 首页 raster 家具规则是开发样本上的窄几何规则，需要新的冻结论文校准误伤率。
- Table 使用保真截图，不恢复语义 cell，也不声称可访问性表格已完成。
- GROBID 未配置时无法稳定区分作者、单位、摘要和正文；Recipe 会诚实保持 degraded。
- 当前环境未安装 Docling，局部模型推理质量尚未验证。

## U02/U03 开发回放

- U02：第 3 页 Table action 覆盖 487 个物理对象；投影到旧开发 FinalLayout 后，目标
  对象在 text region 中剩余 0 个。第 6、7 页各补回两个原生 Figure image。
- U03：首页 `RV0002` residual ROI 与旧伪 Figure `r-g6` bbox 一致，Recipe 将其排除；
  邻近 citation/license 区域 `r-g7` 因没有显式 Figure/Table 锚，从 caption 降为
  margin。ArticleTree 覆盖 1759 个元素，无生成正文。

这些样本已经参与设计与修正，只能证明故障族被显式表达和执行，不能作为泛化指标。
