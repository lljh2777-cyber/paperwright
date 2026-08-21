# Source Evidence v0.1 → v0.2

Source Evidence v0.2 为 E4 局部专家路由增加两个可回放边界；运行时继续接受 v0.1，新的
`layout-prepare` 只写 v0.2。

## 新增字段与产物

- index `status`：`complete`、`degraded` 或 `conflicted`；存在 open conflict 时必须是
  `conflicted`。
- `specialist_requests_path` / `specialist_requests_sha256`：绑定
  `paperwright-specialist-requests-v0.1`。
- summary `specialist_request_count`。
- 默认 provider 集增加 `docling-local`。没有冲突时它以
  `docling_not_requested_no_conflicts` 明确表示未调用；有请求但未启用/未安装时分别记录
  对应 unavailable 原因。

每条 specialist request 必须引用一个 conflict 和已登记 provider，并提供排序去重的
page scope、可选 PaperWright bbox、请求能力及执行状态。v0.2 校验器把 request artifact
纳入 SHA-256 链并检查 page/bbox 边界。

## 兼容性

- v0.1：继续只按原有 providers/alignments/claims/conflicts 与五项 summary 校验。
- v0.2：额外校验 bundle status、specialist request 哈希/引用/范围与六项 summary。
- `layout-apply` 使用同一运行时校验器，因此旧 review bundle 无需迁移即可读取；重新运行
  `layout-prepare` 会生成 v0.2。
