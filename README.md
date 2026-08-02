# Paper2MD

Paper2MD 是一个本地、可追溯、非生成式 AI 的科研 PDF 重建工具。当前版本为
`0.8.0a0` 源码 Alpha。

```text
PDF → PhysicalDocument → article.md + images/ + manifest.json
                         ↘ reviewed layout → public anchors + reader.json
```

## 当前能力

- PDFium 主后端；
- 标题、段落与基础双栏阅读顺序；
- 原生图片、Figure/Caption 分组与保守 region-render；
- 表格无法可靠结构化时明确标记 `degraded`；
- 单文件与确定性批量转换；
- manifest、batch summary、路径安全及原子输出；
- Content ROI、布局候选、结构化复核和最终布局校验；
- `fast`、`standard`、`forensic` 三种布局提取配置；
- 自包含证据包、输出质量报告和无正文训练数据导出；
- 面向阅读器的稳定 Markdown 锚点、Figure/图注关系和 `reader.json`；
- 完全本地运行，不调用 LLM、外部 API 或云 OCR。

region-render 默认关闭，只能显式启用。PDFBox 目前仅保留接口，选择后会明确
报告不可用，不会伪造转换结果。

## 开始之前

需要：

- 64 位 Python 3.10、3.11 或 3.12；
- Git，或者从 GitHub 下载并解压源码 ZIP；
- 首次安装时能够访问 Python Package Index，以取得锁定依赖；
- born-digital（本身含文字层）的 PDF。

Windows 11 / Python 3.11.2 和 Linux / Python 3.12 已实测。macOS、Windows
ARM 和 Linux ARM 尚未验证。

## Windows PowerShell 安装

```powershell
git clone https://github.com/lljh2777-cyber/Paper2MD.git
cd Paper2MD

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .

paper2md --version
paper2md --help
```

如果没有 Git，可在 GitHub 页面选择 **Code → Download ZIP**，解压后在包含
`pyproject.toml` 的目录打开 PowerShell，再从创建虚拟环境开始执行。

## Linux 安装

某些 Linux 发行版需要先通过系统包管理器安装 `python3-venv` 和
`python3-pip`。

```bash
git clone https://github.com/lljh2777-cyber/Paper2MD.git
cd Paper2MD

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .

paper2md --version
paper2md --help
```

`pip install .` 会根据 `pyproject.toml` 自动安装
`pypdfium2==5.11.0` 和 `Pillow==12.2.0`，无需提前手动安装。

## 转换单篇 PDF

```bash
paper2md convert input.pdf output-dir
```

Windows 示例：

```powershell
paper2md convert "C:\Papers\example.pdf" "C:\Papers\example-output"
```

输出目录包含：

```text
output-dir/
├── article.md
├── physical_document.json
├── manifest.json
└── images/
```

输出目录必须尚不存在，Paper2MD 不会覆盖已有数据。

## 批量转换

```bash
paper2md batch output-root --input-dir pdf-directory --continue-on-error
```

`--input-dir` 只读取该目录第一层的 PDF，不递归扫描。每篇论文会写入独立
子目录，并额外生成 `batch_summary.json`。

## 启用保守 region-render

默认关闭。需要时显式启用：

```bash
paper2md convert input.pdf output-dir --region-render-mode auto
```

该模式用于补充部分混合位图/矢量 Figure，但可能保守漏检。

## 混合布局复核

复杂双栏、跨栏 Figure/Table 或页面附属内容较多时，可以使用显式复核流程：

```bash
paper2md layout-prepare input.pdf roi-review --extraction-profile fast
paper2md layout-prepare input.pdf layout-review \
  --content-roi-json roi-review/content-roi.json \
  --review-mode visual-direct
paper2md validate-final-layout layout-review/page-0001/final-layout.json \
  --task layout-review/page-0001/layout-task.json
paper2md layout-apply input.pdf layout-review output-dir --evidence standard
```

第一步生成 Content ROI 提案；确认 ROI 后，第二步生成原页预览和复核说明。
`visual-direct` 是默认审核模式：人工或视觉 AI 以干净的 `page.png` 为准，直接绘制
最终正文、Figure/Table、caption 和页眉页脚区块；审核任务不包含规则候选坐标。
`content-roi.png` 只显示一个粗粒度有效内容框。非排除区块必须位于已确认 ROI 内，
Paper2MD 在回接原始 PDF 元素时也会过滤 ROI 外围内容，避免页眉、页脚和页码中断正文。
ROI 必须包含标题、作者、脚注、Figure/Table 和 caption；提案过窄时应先修正并重新确认。
旧的 `visual-direct` 审核包若没有 `metadata.analysis_roi`，必须重新运行 `layout-prepare`，
不会在 `layout-apply` 时静默放宽或迁移边界。
旧的规则叠加流程可通过 `--review-mode candidate-assisted` 显式启用。审核者只填写
结构化 `final-layout.json`，不转录论文正文。Paper2MD 随后
重新校验输入、缓存、栅格证据、审核任务完整性和最终区块关系，再确定性生成结果。

混合布局输出的正文包含 `p2md:block` / `p2md:slot` 隐藏锚点，
`_paper2md/reader.json` 通过稳定 ID 连接正文块、视觉槽位、图片和图注。阅读器
应以该文件为主索引，不依赖 Markdown 行号、标题 slug 或图注文本匹配。可独立验包：

```bash
paper2md validate-reader output-dir/_paper2md/reader.json
```

`fast` 使用原生文字坐标和低分辨率栅格证据；`standard` 仅把高风险页面升级为
完整对象分析；`forensic` 对全文执行完整对象遍历。

## 找不到 `paper2md` 命令

确认虚拟环境已激活，或直接使用：

```bash
python -m paper2md --help
python -m paper2md convert input.pdf output-dir
```

关闭终端后，需要重新激活 `.venv`。

## 更多文档

混合布局流程的最终结果采用自包含文档包：顶层只放 `article.md` 和
`images/`，ROI、布局覆盖图、追溯数据与验证报告统一放入 `_paper2md/`。
`_paper2md/reader.json` 属于所有证据级别都会保留的功能索引，不是调试证据。
`layout-apply` 默认使用 `--evidence standard`；也可选择 `minimal` 或
`full`，并通过 `--include-source-pdf` 显式复制原 PDF。完整结构与命令见
[混合布局设计](docs/HYBRID_LAYOUT_OUTLINE_ZH.md)。

标准与完整证据包会自动检查 Markdown 断词、重复词、短碎片、疑似图内标签、
标题完整性、图片链接、manifest 清单，以及布局对象是否遗漏或重复归属。
启发式文本问题标为 `warning`，不会阻断输出；确定性结构检查单独给出
`PASS/FAIL`。

- [Alpha 快速开始](docs/QUICKSTART_ALPHA.md)
- [配置参考](docs/CONFIGURATION.md)
- [架构](docs/ARCHITECTURE.md)
- [支持矩阵](docs/SUPPORT_MATRIX.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [Alpha RC 说明](docs/ALPHA_RC_RELEASE_NOTES.md)
- [manifest v0.8 与 Reader 迁移说明](docs/MANIFEST_MIGRATION_V0.8.md)

## AI Agent skills

仓库的 `skills/` 目录提供三个可分发的 Agent skill：

- [`paper2md-install`](skills/paper2md-install/SKILL.md)：下载、安装与 CLI 验证；
- [`paper2md-convert`](skills/paper2md-convert/SKILL.md)：直接转换、批量转换与人工/视觉 AI 混合复核；
- [`paper2md-contribute`](skills/paper2md-contribute/SKILL.md)：理解架构、修改代码、测试和参与贡献。

支持仓库内 skill 发现的 Agent 可以直接加载 `skills/`。其他工具可把对应 skill
目录复制到自身的 skills 目录，或在提示中明确要求参照对应 `SKILL.md`。安装后可使用
`$paper2md-install`、`$paper2md-convert` 或 `$paper2md-contribute` 显式调用。

这些 skills 只指导 Agent 调用现有命令和遵守项目契约，不会替 Paper2MD 隐式增加
联网、LLM、OCR 或外部 API 行为。

调用 skill 后，Agent 会先询问尚未明确、且会影响质量、输出范围、存储、隐私或失败
处理的关键选项，并给出推荐设置；已经由用户指定或可从环境安全判断的选项不会重复
询问。用户也可以明确要求使用推荐设置。混合布局中的 Content ROI 和最终布局视觉复核
仍是必须单独完成的确认步骤。

## 已知限制

- 不支持扫描 PDF 的 OCR；
- 不生成语义表格或公式 LaTeX；
- 纯矢量且缺少可靠 Figure 证据时会保守拒绝；
- PDFBox、GUI、Web/API 服务和容器不在当前范围；
- macOS 和 ARM 平台尚未验证。

## 版本与数据契约

包版本与数据契约独立演进。当前包版本为 `0.8.0a0`，PhysicalDocument 使用
v0.2，布局任务使用 v0.1/v0.2，当前混合布局 manifest 使用 v0.8，Reader
使用 v0.1。直接转换的兼容模式仍可能输出 manifest v0.4 或 v0.5；读取旧结果时
继续接受混合布局 manifest v0.6/v0.7。升级包版本不代表已有数据契约会被隐式
改写。

## 许可证与分发

Paper2MD 采用 [Apache License 2.0](LICENSE)。任何人都可以在遵守许可证条件的
前提下使用、复制、修改和分发本项目。分发本项目或衍生作品时，请同时保留
`LICENSE` 和 `NOTICE` 中要求保留的声明。

完整的阶段研发记录和历史验证证据保存在
`agent/v2-rebuild` 分支。
