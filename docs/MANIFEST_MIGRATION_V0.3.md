# Manifest v0.3 迁移说明

Phase 3 将 `manifest_version` 从 `paperwright-manifest-v0.2` 升至
`paperwright-manifest-v0.3`。原有必填字段、`elements`、`images`、
`degraded` 和 `physical_document` 保持含义兼容；新增：

- `figures`：同页、确定性 Figure group，包含原生成员、bbox、输出资产、
  caption association、矢量对象证据摘要和 Markdown 放置方式。
- `figure_rejections`：未提升为 Figure 的原生图片对象及明确过滤原因。
- `images[*].figure_group_id` 与 `markdown_referenced`：原始资产继续保留，
  同时说明其是否属于组合 Figure、是否由 Markdown 直接引用。

`grouped` 资产是按 PDF 坐标合成的原生位图，不宣称恢复未渲染的矢量内容；
矢量对象仅作为 `vector_evidence` 追溯。`embedded` 表示直接引用单个原生
位图。低置信、冲突、跨页或无 caption 时不会强配，状态为
`ambiguous`/`none` 并保持页末降级放置。
