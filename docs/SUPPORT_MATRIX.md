# Paper2MD Alpha 支持矩阵

| 平台 / 运行时 | 状态 | 说明 |
|---|---|---|
| Linux / Python 3.12 | 已实测 | Phase 6 Alpha RC 云端安装与端到端检查通过 |
| Windows / Python 3.11.2 | 已实测 | Alpha 基线的 batch 与安装检查通过；混合布局完整套件记录为 219 passed、8 subtests passed |
| macOS | 未验证 | 不声明支持 |
| Python 3.10–3.12 | 声明范围 | 打包元数据与测试约束 |
| PDFium | 主后端 | 锁定验证组合为 pypdfium2 5.3.0 / PDFium 145.0.7616.0 |
| PDFBox | 不可用 | 仅接口占位；选择时明确失败 |

## 当前文档范围

- born-digital PDF；
- 标题、段落、双栏、图片和保守 Figure/Caption；
- Content ROI、布局候选、结构化复核、布局应用及质量证据包；
- `fast`、按页选择性升级的 `standard` 和完整对象 `forensic` 提取；
- region-render 默认关闭，`auto` 为显式 opt-in；
- 表格不可靠时输出 `degraded`，不伪造语义结构。

## 不在当前范围

- OCR 与扫描 PDF；
- 语义表格和公式 LaTeX；
- 完整 PDFBox 实现；
- GUI、Web/API 服务、容器、PyPI 和正式二进制发布。

项目源码采用 Apache License 2.0，分发时需保留 `LICENSE` 和 `NOTICE`。
第三方 PDFium/pypdfium2 二进制及其 bundled notices 仍应按具体分发形式单独审查；
项目许可证与第三方依赖审计是两个不同层面的结论。
