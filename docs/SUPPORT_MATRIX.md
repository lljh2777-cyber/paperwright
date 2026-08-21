# PaperWright Alpha 支持矩阵

| 平台 / 运行时 | 状态 | 说明 |
|---|---|---|
| Linux / Python 3.12 | 已实测 | Phase 6 Alpha RC 云端安装与端到端检查通过 |
| Windows / Python 3.11.2 | 已实测 | 2026-08-03 维护验证为 268/268 单测、13/13 内容 smoke，wheel/sdist 安装检查通过 |
| macOS | 未验证 | 不声明支持 |
| Python 3.10–3.13 | 声明范围 | 打包元数据与测试约束；3.13 已通过安装与转换验证 |
| Python 3.13 | 已实测 | 2026-08-13 安装、单测与端到端转换验证通过 |
| PDFium | 主后端 | 锁定验证组合为 pypdfium2 5.11.0 / PDFium 151.0.7920.0 |
| pdfplumber | 默认几何侧车 | 锁定 0.11.10；只提供独立 observation 与 table proposal |
| GROBID | 可选本地语义侧车 | 通过 `PAPERWRIGHT_GROBID_URL` 启用；当前 HTTP 服务未实测 |
| Docling | 可选局部专家 | 仅在冲突请求存在且设置 `PAPERWRIGHT_DOCLING_ENABLED=1` 时按页运行；当前本机模型未实测，JSON/provenance 适配已有离线测试 |
| PDFBox | 不可用 | 仅接口占位；选择时明确失败 |

## 当前文档范围

- born-digital PDF；
- 标题、段落、双栏、图片和保守 Figure/Caption；
- Content ROI、布局候选、结构化复核、布局应用及质量证据包；
- 混合布局包的公开 Markdown 锚点、Reader v0.1 索引及独立完整性校验；
- Text Task/Review v0.1/v0.2、manifest v0.10 文本复核派生包、manifest v0.11 L3 合成溯源派生包及父包哈希链；
- 纯 PDFium `fast`、PDFium inventory + pdfplumber 的 `standard` 和完整对象 `forensic` 提取；
- PaperRecipe v0.1 受限结构动作、ArticleTree v0.1 元素守恒编译和确定性重放；
- region-render 默认关闭，`auto` 为显式 opt-in；
- 表格不可靠时输出 `degraded`，不伪造语义结构。
- Recipe 的模型 producer 尚未开放；当前实现是同契约的确定性 baseline producer。

## 不在当前范围

- OCR 与扫描 PDF；
- 语义表格和公式 LaTeX；
- 完整 PDFBox 实现；
- GUI、Web/API 服务、容器、PyPI 和正式二进制发布；
- Reader 尚不提供正文中 `Fig. N` 引用的语义识别；能力字段明确报告
  `body_references: unavailable`。

项目源码采用 Apache License 2.0，分发时需保留 `LICENSE` 和 `NOTICE`。
第三方 PDFium/pypdfium2 二进制及其 bundled notices 仍应按具体分发形式单独审查；
项目许可证与第三方依赖审计是两个不同层面的结论。
