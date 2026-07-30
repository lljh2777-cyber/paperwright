# Paper2MD 0.6.0a0 Alpha RC 支持矩阵

| 维度 | 状态 | 证据或限制 |
|---|---|---|
| Python 3.10–3.13 | 支持范围 | `pyproject.toml` 为 `>=3.10,<3.14` |
| Linux Work 云端 | 本轮实测 | 全量测试及 wheel/sdist 隔离安装 |
| Windows / Python 3.11.2 | Phase 5 已实测 | `phase5_alpha/windows_validation*`；本轮未重测 |
| macOS | 未验证 | 不作兼容承诺 |
| PDFium / pypdfium2 5.3.0 | 主后端 | PDFium 145.0.7616.0 |
| Pillow 12.2.0 | 支持 | PNG 资产编码 |
| PDFBox | 不可用接口 | 选择后明确失败，不伪装成功 |
| born-digital PDF | Alpha 支持 | 复杂出版商版式仍可能 degraded |
| 扫描 PDF/OCR | 不支持 | 不调用本地或云 OCR |
| region-render off | 默认 | manifest v0.4 |
| region-render auto/explicit | opt-in | manifest v0.5，保守拒绝 |
| 批处理 | 支持 | 非递归、确定排序、逐文档原子隔离 |
| 语义表格/公式 LaTeX | 不支持 | 表格不可靠时诚实 degraded |
| LLM/API/生成式 AI | 不使用 | 本地确定性规则 |
| 源码公开再分发 | 未批准 | 项目级许可证 `NOASSERTION` |
| wheel/PDFium 二进制分发 | 未批准 | bundled notices/agg23 待复核 |
