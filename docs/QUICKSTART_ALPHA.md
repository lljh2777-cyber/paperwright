# paperwright 快速开始（Alpha）

> 更完整的工作流、命令与成本说明见 [用户指南](USER_GUIDE.md)。

paperwright 是本地、可追溯、非生成式 AI 的科研 PDF 重建工具：PDF → Markdown +
图片 + 清单 + 溯源。当前版本 `0.9.0a0`。

支持范围：64 位 Python 3.10–3.13；Windows 11 x64、Linux x64；born-digital
（含文字层）科研 PDF。macOS、Windows ARM、Linux ARM 尚未验证。

## 一、安装：三条路径按需选择

| 路径 | 适合 | 命令 |
|---|---|---|
| **0. 纯 CLI** | 只转换，不碰 agent | `pip install paperwright` |
| **1. Agent 完整体验** | 要在 agent 里用（skills + 混合复核） | `curl -fsSL https://raw.githubusercontent.com/lljh2777-cyber/paperwright/main/install.sh \| bash` |
| **2. + qwen 视觉复核** | 让视觉模型顶替人眼复核版面 | 路径 1 后加 `bash install.sh install --with-vision` |

路径 1 安装器自动：检测 agent harness（Claude Code / Codex / Cursor / Gemini）→
选择 3.10–3.13 Python（必要时用 uv）→ 装 CLI → 复制 4 个 skills 到
`~/.claude/skills` → 验证。**安装后需重启 agent 会话**。

路径 2 的 `--with-vision` 额外把 qwen-mm-plugins 的两个视觉 MCP（api/core）写入
Claude Code 配置，无需再单独下载插件；使用时还需设置
`DASHSCOPE_API_KEY`（视觉走云端）。未装不影响其他功能。

手动安装（Windows PowerShell / Linux）：

```bash
git clone https://github.com/lljh2777-cyber/paperwright.git
cd paperwright
python3 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
paperwright --version
```

## 二、最小转换

```bash
paperwright convert paper.pdf output-dir
```

输出目录（必须尚不存在）：

- `article.md` — 重建的 Markdown（含 `pwwd:block` 锚点）
- `images/` — 图片
- `manifest.json` / `physical_document.json` — 清单与溯源

批量：

```bash
paperwright batch output-root --input-dir papers --continue-on-error
```

跨列/跨页断句会在转换时自动拼接；页眉/页脚/页码默认自动剔除
（`--furniture`）。单文档可用 `paperwright validate-article-model` 校验。

## 三、在 Agent 中使用

重启会话后，向 agent 说（或直接引用对应 skill）：

```
用 $paperwright-convert 把这个 PDF 转成 markdown
```

agent 会：先问关键选项 → 执行转换 → 校验 → **按下文清单向你汇报**。

复杂双栏/跨栏图走混合复核时，agent 会自动编排
`layout-prepare → 视觉复核 → layout-apply → validate`。装了 `--with-vision`
时视觉复核可由 qwen 顶替（`$paperwright-vision-qwen`）；未装则走人眼。

## 四、Agent 必须向用户汇报什么

> 本节是给执行转换的 agent 的硬性汇报清单。任何一次转换，agent 都应在
> 转换前、转换中、转换后如实汇报以下内容；**不得隐瞒失败、跳过校验或用
> "看起来对了"代替校验结果**。

### 转换前（先问清再动手）

1. **选用的工作流与原因**：direct（普通双栏）/ hybrid（复杂版面）/ batch；
2. **输出目录**：新目录路径（paperwright 拒绝覆盖已有目录）；
3. **视觉复核方式**：人眼 / 视觉模型 / 跳过 —— 若调用外部视觉服务，必须
   明确告知用户"这里会调用外部 AI 服务并上传页面图片"，征得同意后才做；
4. **影响结果的关键选项**：`--furniture`、`--region-render-mode`、
   参考文献处理、是否复制源 PDF。

未明确的选项不要自作主张；用户已指定的不要重复问。

### 转换中（进程汇报）

1. 每个阶段状态：`extract → (prepare → review → apply) → validate`；
2. **拼接与剔除**：合并了几处跨列/跨页段落（join-blocks）、剔除了哪些
   页眉/页脚/页码；
3. 每条 `warning`：如表格结构 degraded、图注未匹配、家具保守保留等 ——
   标为"提示"，不是失败。

### 转换后（结果汇报）

1. **校验结果**：逐项报告 `validate-article-model` / `validate-reader` /
   `validate-text-review` 等的结果；`PASS` / 警告 / **阻断性 `FAIL`** 分开列；
2. **交付物清单**：`article.md`、图片数量、`_paperwright/`（article-model、
   reader.json、验证报告）、manifest 路径；
3. **已知限制**：该 PDF 里未做到的事——扫描件（无 OCR）、表格语义行/列未
   重建、公式未转 LaTeX、某些图被保守拒绝等；
4. **隐私声明**：明确说明"PaperWright 核心未调用任何模型/外部 API"；若本次
   用了视觉复核，补充"页面图片发给了外部视觉服务"。

汇报示例结尾：

> 转换完成：17 页，7 张图，2 处跨列段落已拼接，页眉页脚已剔除。
> 校验：article-model PASS，reader PASS，2 条警告（1 张表格 degraded，
> 1 条图注未匹配）。限制：无 OCR。PaperWright 核心本地运行，未调用外部服务。

## 五、视觉复核（可选）

```bash
bash install.sh install --with-vision
export DASHSCOPE_API_KEY=sk-ws-...
```

重启会话后可用 `$paperwright-vision-qwen`：视觉模型负责 Content ROI 建议、
visual-direct 区域、join-blocks 断句确认、图注核查。所有输出仍走本地校验器
验收。未配 key 或未装视觉时，`paperwright-convert` / `paperwright-agent-workflow`
的人眼复核流程完整可用。

## 六、批处理行为（备忘）

- `--input-dir` 只扫描第一层 `.pdf`，不递归；
- 输入确定性排序；每篇写入独立目录；目录已存在时拒绝覆盖；
- 文档失败不留半成品目录；默认首个失败停止，`--continue-on-error` 继续但
  最终返回非零退出码；
- `batch_summary.json` 不记录绝对输入路径或凭据。

## 七、故障排查

- 命令找不到 → 确认 venv 已激活或 `python -m paperwright --help`；
- 输出目录已存在 → 换新目录；
- 视觉工具不可用 → 检查 `--with-vision` 是否执行、`DASHSCOPE_API_KEY` 是否
  设置、是否重启了会话；
- 更多见 `docs/TROUBLESHOOTING.md`。
