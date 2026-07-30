# Alpha 配置参考

示例：`config/alpha.example.json`。

优先级固定为：

```text
内置默认值 < --config JSON < 显式 CLI 参数
```

未在 CLI 上明确提供的值不会覆盖配置文件。

## 主要字段

| 字段 | 默认 | Alpha 说明 |
|---|---|---|
| `backend` | `pdfium` | `pdfbox` 仅接口，会明确失败 |
| `contract_version` | `paper2md-physical-document-v0.2` | 其他版本拒绝 |
| `limits.max_pages` | 2000 | 每文档页数上限 |
| `limits.max_output_bytes` | 536870912 | 每文档输出硬上限 |
| `limits.timeout_seconds` | 120 | 当前为后端配置边界，尚非跨平台硬 sandbox |
| `output.allow_existing_directory` | false | Alpha 强制 false |
| `output.atomic_write` | true | Alpha 强制 true |
| `region_render.mode` | `off` | `off/explicit/auto` |
| `region_render.max_candidates_per_document` | 12 | 1–100 |
| `workspace_root` | null | 设置后输出不得越界 |

配置是严格 JSON：未知字段、错误类型和不安全 output policy 会直接拒绝。

单文档 explicit 调试：

```bash
paper2md convert input.pdf out \
  --region-render-mode explicit \
  --region-render-page 2
```

batch 只允许 `off` 或 `auto`，不支持逐文档 explicit page。

使用配置并由 CLI 覆盖：

```bash
paper2md batch out \
  --input-dir papers \
  --config config/alpha.example.json \
  --region-render-mode auto \
  --region-render-max-candidates 4
```
