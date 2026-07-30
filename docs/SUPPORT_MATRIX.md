# Paper2MD Alpha 支持矩阵

| 平台 / 运行时 | 状态 | 说明 |
|---|---|---|
| Linux / Python 3.12 | 已实测 | Phase 6 Alpha RC 云端安装与端到端检查通过 |
| Windows / Python 3.11.2 | 已实测 | 本地 100/100 单测、batch 8/8、安装命令 12/12 |
| macOS | 未验证 | 不声明支持 |
| Python 3.10–3.12 | 声明范围 | 打包元数据与测试约束 |
| PDFium | 主后端 | 锁定验证组合为 pypdfium2 5.3.0 / PDFium 145.0.7616.0 |
| PDFBox | 不可用 | 仅接口占位；选择时明确失败 |

## 当前文档范围

- born-digital PDF；
- 标题、段落、基础双栏、图片和保守 Figure/Caption；
- region-render 默认关闭，`auto` 为显式 opt-in；
- 表格不可靠时输出 `degraded`，不伪造语义结构。

## 不在当前范围

- OCR 与扫描 PDF；
- 语义表格和公式 LaTeX；
- 完整 PDFBox 实现；
- GUI、Web/API 服务、容器、PyPI 和正式二进制发布。

项目许可证仍为 `NOASSERTION`。支持矩阵只描述工程验证范围，不代表公开
再分发许可已经通过。
