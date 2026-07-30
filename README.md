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

当前候选源码是 **Phase 5 Alpha**：在 Phase 4 默认关闭、显式 opt-in 的
通用 auto region-render 之上，增加标准源码安装、批处理、机器可读诊断和
可复现验证：

- 可调用的单文件 CLI/API；
- pypdfium2/PDFium 薄适配器；
- `PDF → PhysicalDocument → article.md + images/ + manifest.json`；
- 可追溯元素、页码、bbox、提取方法、输出哈希；
- 基础双栏几何顺序、嵌入位图提取和诚实的表格降级；
- 同页 Figure 候选分组、明确 Figure/Fig. caption 的保守配对与
  Markdown 邻接；
- 对低置信、歧义、无 caption、跨页或未渲染矢量证据明确降级；
- `off`（默认）、`explicit`（调试）和 `auto` 三种模式；
- `auto` 仅依据同页 Figure group、显式 caption、原生图片、矢量和图内
  文字几何证据，用 PDFium 对获准 bbox 做真正的 clipped page render；
- 原生位图/组合资产始终保留作追溯；拒绝时保留旧资产并记录 degraded
  reason，不把局部裁剪冒充完整 Figure；
- 自生成 born-digital PDF fixture、路径安全、原子输出及逐字节确定性测试。
- `paper2md batch` 非递归、确定性排序、逐文档原子隔离；
- batch summary 对 corrupt/unsupported/backend unavailable/output
  conflict/path safety/configuration/internal 分类；
- wheel 与 sdist 仅用于临时安装测试，不随 source-only 交付提交。

PDFBox 仍只是显式不可用的对照/回退边界。本 spike 不默认打开区域渲染，
也不实现 OCR、语义表格、公式 LaTeX、纯矢量语义解析或深层图像理解。

Stage C 使用 8 篇新选择的 CC BY born-digital OA 论文做小规模真实版式
验证；结果和明确限制见
[`realworld/report_zh.md`](realworld/report_zh.md)。该 corpus 不是已丢失
Phase 1B/2 检查点的恢复，PDF 和转换输出不会提交到仓库；8 篇结果也不
外推为全部真实出版商版式泛化。

Phase 3 的实现、8 篇复核结果和限制见
[`phase3/report_zh.md`](phase3/report_zh.md)。尤其是 RW2-005：
碎片对象已能确定性组合并与 caption 邻接，但混合位图/矢量的完整区域
重建仍标为 degraded。

受限 region-render spike 的冻结边界、机器证据与视觉结论见
[`phase4_render_spike/report_zh.md`](phase4_render_spike/report_zh.md)。
通用 auto 模式的冻结规则、8 篇回归、全量获准候选视觉检查与限制见
[`phase4_auto_region/report_zh.md`](phase4_auto_region/report_zh.md)。
auto 仍默认关闭，也不代表发布级能力或真实出版商总体泛化。

## Alpha 快速开始

支持 Python 3.10–3.13，核心依赖锁定为 `pypdfium2==5.3.0` 和
`Pillow==12.2.0`。

```bash
python -m pip install .
paper2md --version
paper2md --help
paper2md convert input.pdf output-dir
paper2md batch batch-output --input-dir papers --continue-on-error
paper2md validate-model physical_document.json
```

从源码直接开发：

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m paper2md --version
PYTHONPATH=src python -m paper2md convert input.pdf output-dir
PYTHONPATH=src python -m paper2md convert input.pdf output-dir \
  --region-render-mode auto
PYTHONPATH=src python -m paper2md validate-model \
  tests/fixtures/physical_document.minimal.json
```

完整复现命令见 [REPRODUCE.md](REPRODUCE.md)，架构边界见
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

更完整的 Linux/PowerShell 示例、配置与故障排查：

- [Alpha Quickstart](docs/QUICKSTART_ALPHA.md)
- [配置参考](docs/CONFIGURATION.md)
- [故障排查](docs/TROUBLESHOOTING.md)

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

后续产品开发应在评审分支中进行，不直接修改 `main`。Alpha 不是正式
release，不包含 OCR、服务端、GUI 或二进制分发。

## 许可证状态

v2 MVP 源码尚未声明最终项目许可证。PDFium、pypdfium2、PDFBox 及其
传递依赖的分发义务将在后续阶段单独核验。历史研究中的
`agg23=NOASSERTION` 只表示正式分发尚未获批，不构成本地接口设计的已确认
冲突。
