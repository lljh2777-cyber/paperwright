# Manifest v0.5 迁移说明

Phase 4 通用 region-render 仅在显式启用 `explicit` 或 `auto` 模式时输出
`paper2md-manifest-v0.5`。默认 `off` 模式仍逐字节输出 v0.4，避免静默改变
既有工作流。

v0.5 新增顶层 `region_render_policy`：

- `mode`：`explicit` 或 `auto`；
- `page_indices`：仅 explicit 模式使用的零基页索引；
- `max_candidates_per_document`：auto 候选硬上限。

Figure 的既有 `native_asset`、`region_render`、`vector_evidence` 和 caption
追溯字段保持兼容。拒绝项继续写入 `figure_rejections`；如果拒绝可归属于
已有 Figure group，该组的 `region_render.status` 为 `rejected`，且
`degraded_reasons` 包含机器可读原因。只有所有候选与像素守卫均通过后，
`asset`/Markdown 才指向 `region-rendered` PNG；`native_asset` 始终保留。

读取方应按 `manifest_version` 分支：

1. v0.4：不得期待 `region_render_policy`；
2. v0.5：必须校验 `region_render_policy`，并将 `rejected` 视为诚实回退，
   不能视为转换失败或自行替换原生资产。

`src/paper2md/schemas/manifest.schema.json` 同时验证 v0.4 与 v0.5，并使用
Draft 2020-12 条件约束保证策略字段仅出现在 v0.5。
