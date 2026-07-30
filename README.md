# Paper2MD

Paper2MD 是一个本地、非生成式 AI 的科研 PDF 转换工具。当前版本为
`0.6.0a0` 源码 Alpha。

输入输出：

```text
PDF → PhysicalDocument → article.md + images/ + manifest.json
```

## 当前能力

- PDFium 主后端；
- 标题、段落与基础双栏阅读顺序；
- 原生图片、Figure/Caption 分组与保守 region-render；
- 表格无法可靠结构化时明确标记 `degraded`；
- 单文件与确定性批量转换；
- manifest、batch summary、路径安全及原子输出；
- 完全本地运行，不调用 LLM、外部 API 或云 OCR。

region-render 默认关闭，只能显式启用。PDFBox 目前仅保留接口，选择后会明确
报告不可用，不会伪造转换结果。

## 安装

需要 Python 3.10–3.12。源码 Alpha 锁定的验证依赖为：

```text
pypdfium2==5.3.0
Pillow==12.2.0
```

在项目根目录安装：

```bash
python -m pip install .
paper2md --version
paper2md --help
```

转换单篇 PDF：

```bash
paper2md convert input.pdf output-dir
```

批量转换：

```bash
paper2md batch output-root --input-dir pdf-directory
```

验证 PhysicalDocument：

```bash
paper2md validate-model tests/fixtures/physical_document.minimal.json
```

更多说明：

- [Alpha 快速开始](docs/QUICKSTART_ALPHA.md)
- [配置参考](docs/CONFIGURATION.md)
- [架构](docs/ARCHITECTURE.md)
- [支持矩阵](docs/SUPPORT_MATRIX.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [Alpha RC 说明](docs/ALPHA_RC_RELEASE_NOTES.md)

## 已知限制

- 不支持扫描 PDF 的 OCR；
- 不生成语义表格或公式 LaTeX；
- 纯矢量且缺少可靠 Figure 证据时会保守拒绝；
- PDFBox、GUI、Web/API 服务和容器不在当前范围；
- macOS 尚未验证。

## 许可证与分发

当前项目级许可证仍为 `NOASSERTION`。项目所有者控制范围内的源码审阅、本地
安装和试用可以继续，但公开 source-only 包、wheel、sdist 和包含 PDFium 的
二进制分发尚未获批准。本仓库当前不构成正式 Release 或公开分发许可声明。

完整的阶段研发记录和历史验证证据保存在
`agent/v2-rebuild` 分支。
