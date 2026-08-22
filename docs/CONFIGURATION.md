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
| `contract_version` | `paperwright-physical-document-v0.2` | 其他版本拒绝 |
| `limits.max_pages` | 2000 | 每文档页数上限 |
| `limits.max_output_bytes` | 536870912 | 每文档输出硬上限 |
| `limits.timeout_seconds` | 120 | 当前为后端配置边界，尚非跨平台硬 sandbox |
| `output.allow_existing_directory` | false | Alpha 强制 false |
| `output.atomic_write` | true | Alpha 强制 true |
| `region_render.mode` | `off` | `off/explicit/auto` |
| `region_render.max_candidates_per_document` | 12 | 1–100 |
| `workspace_root` | null | 设置后输出不得越界 |

配置是严格 JSON：未知字段、错误类型和不安全 output policy 会直接拒绝。

可选 GROBID 不写入 JSON 配置，避免把本地服务状态误当成可重放文档参数。启动本地服务后
设置环境变量，例如：

```bash
export PAPERWRIGHT_GROBID_URL=http://127.0.0.1:8070
```

未设置或请求失败时，转换不会伪装成“论文没有语义结构”；SourceEvidenceBundle 会记录
`grobid-scholarly` provider 为 `unavailable`。GROBID 返回的 TEI 只提供 proposed claims，
没有直接替换 PDF 原生正文的权限。

本地 CRF 服务的已验证版本、启动命令、健康检查和资源边界见
[GROBID CRF 本地侧车](GROBID_LOCAL.md)。

Docling 同样不写入项目 JSON 配置，也不是默认依赖。只有 SourceEvidenceBundle 已产生
局部 specialist request 时，以下显式开关才会加载本机安装的 Docling：

```bash
export PAPERWRIGHT_DOCLING_ENABLED=1
```

PaperWright 使用 Docling 的 `page_range` 逐个转换请求页，并在接入层继续过滤到请求 ROI。
未启用、依赖未安装、请求页失败和“没有冲突所以未请求”有不同 diagnostics；Docling
输出只能形成 proposed layout/table/reading-order claims，不能替换原生正文或直接写
Markdown。

单文档 explicit 调试：

```bash
paperwright convert input.pdf out \
  --region-render-mode explicit \
  --region-render-page 2
```

batch 只允许 `off` 或 `auto`，不支持逐文档 explicit page。

使用配置并由 CLI 覆盖：

```bash
paperwright batch out \
  --input-dir papers \
  --config config/alpha.example.json \
  --region-render-mode auto \
  --region-render-max-candidates 4
```
