# Alpha 故障排查与限制

## 错误分类

| category | 含义 | 常见处理 |
|---|---|---|
| `corrupt` | PDFium 无法打开或 PDF 损坏 | 换用合法完整的 born-digital PDF |
| `unsupported` | 输入类型不支持 | 确认扩展名和 Alpha 范围 |
| `backend_unavailable` | 后端未绑定 | 安装锁定 PDFium；PDFBox 当前必然失败 |
| `output_conflict` | 输出已存在或与输入嵌套 | 使用全新独立输出目录 |
| `path_safety` | 路径或 symlink 越界 | 使用常规文件及明确 workspace |
| `configuration` | JSON/CLI 配置无效 | 对照配置参考 |
| `internal` | 未分类产品错误 | 保留 summary 和 stderr 后报告 |

## 常见问题

### PDFBox 为什么失败？

Alpha 只保留 PDFBox 接口，没有捆绑或执行 JAR。选择
`--backend pdfbox` 会返回明确的 `backend_unavailable`，不会伪造成功。

### 扫描 PDF 为什么没有正文？

Alpha 不做 OCR。扫描件应被视为 unsupported/degraded 输入，而不是文字恢复
成功。

### 为什么表格不是 Markdown 表？

当前只在结构不可靠时保留文字并标记 `degraded`，不会猜测行列。

### 为什么有些纯矢量 Figure 没有 region asset？

auto 模式要求可追溯的 native Figure group；纯矢量且没有 native group 时
保守拒绝。Phase 4 的安全阈值本阶段没有修改。

### manifest v0.4 与 v0.5

- region render `off`：manifest v0.4，保持旧默认输出兼容；
- `explicit/auto`：manifest v0.5，增加 `region_render_policy` 和区域证据。

详见 `docs/MANIFEST_MIGRATION_V0.5.md`。

## 明确不支持

- OCR/扫描 PDF 识别；
- 语义表格和公式 LaTeX；
- 深层 Figure/caption 语义；
- PDFBox 完整后端；
- GUI、Web 服务、容器或公开 PyPI release；
- Windows 已验证声明（由本地门禁另行验证）。
