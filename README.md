# Paper2MD

Paper2MD 是一个面向科研论文的、本地优先、可追溯的 PDF 转 Markdown
项目。目标是在不调用生成式 AI、外部 LLM API 或云端 OCR 的前提下，
生成结构化、可复现的 Markdown 阅读层。

## v2 重建说明

此前的临时 Work 执行环境及其阶段产物已被平台清理。本仓库用于新的 v2 重建流程；新结果不得被表述为原 Phase 1B / Phase 2 检查点的恢复结果。

计划中的最小流水线：

```text
PDF → PhysicalDocument → article.md + images/ + manifest.json
```

MVP 暂定使用 PDFium 作为主后端，PDFBox 作为对照或回退。

当前候选提交范围已推进到 **v2-realworld（Stage C）**：

- 可调用的单文件 CLI/API；
- pypdfium2/PDFium 薄适配器；
- `PDF → PhysicalDocument → article.md + images/ + manifest.json`；
- 可追溯元素、页码、bbox、提取方法、输出哈希；
- 基础双栏几何顺序、嵌入位图提取和诚实的表格降级；
- 自生成 born-digital PDF fixture、路径安全、原子输出及逐字节确定性测试。

PDFBox 仍只是显式不可用的对照/回退边界。本阶段不实现 OCR、语义表格、
公式 LaTeX 或 Figure/Caption 邻接。

Stage C 使用 8 篇新选择的 CC BY born-digital OA 论文做小规模真实版式
验证；结果和明确限制见
[`realworld/report_zh.md`](realworld/report_zh.md)。该 corpus 不是已丢失
Phase 1B/2 检查点的恢复，PDF 和转换输出不会提交到仓库；8 篇结果也不
外推为全部真实出版商版式泛化。

## 快速开始

要求 Python 3.10+、`pypdfium2==5.3.0` 和 `Pillow==12.2.0`。

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m paper2md --version
PYTHONPATH=src python -m paper2md convert input.pdf output-dir
PYTHONPATH=src python -m paper2md validate-model \
  tests/fixtures/physical_document.minimal.json
```

开发安装：

```bash
python -m pip install -e .
paper2md --version
```

完整复现命令见 [REPRODUCE.md](REPRODUCE.md)，架构边界见
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 仓库存储边界

仓库可以保存：

- 源码与测试
- 小型自生成 fixtures
- schema 与配置
- 开放获取论文的来源、许可证和哈希清单
- 机器可读摘要、人工检查结果、复现命令与文档

仓库不保存：

- 论文 PDF
- 大型转换输出
- PDFium、JAR 或其他二进制文件
- 凭据或令牌

后续产品开发应在评审分支中进行，不直接修改 `main`。

## 许可证状态

v2 MVP 源码尚未声明最终项目许可证。PDFium、pypdfium2、PDFBox 及其
传递依赖的分发义务将在后续阶段单独核验。历史研究中的
`agg23=NOASSERTION` 只表示正式分发尚未获批，不构成本地接口设计的已确认
冲突。
