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

当前提交范围仅为 **v2-bootstrap**：

- Python 包、CLI/API 和异常边界；
- `PhysicalDocument` 核心数据模型；
- manifest 与 PhysicalDocument JSON Schema；
- PDFium/PDFBox 可替换后端接口（不含二进制或实现）；
- 自生成 fixture 和标准库测试；
- 路径安全、确定性序列化和仓库存储政策。

bootstrap 阶段不会真正解析 PDF。`paper2md convert` 在后端未安装时会以
非零状态明确失败，而不是生成伪输出。

## 快速开始

要求 Python 3.10+，bootstrap 本身无第三方运行时依赖。

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m paper2md --version
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

bootstrap 源码尚未声明最终项目许可证。PDFium、pypdfium2、PDFBox 及其
传递依赖的分发义务将在后续阶段单独核验。历史研究中的
`agg23=NOASSERTION` 只表示正式分发尚未获批，不构成本地接口设计的已确认
冲突。
