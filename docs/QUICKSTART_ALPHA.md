# PaperWright Alpha 快速开始

## 支持范围

- 默认剔除跨页重复页眉/页脚/页码（`convert --furniture`，`keep`/`strip`/`auto`）；

- 64 位 Python 3.10–3.13；
- Windows 11 x64 与 Linux x64；
- born-digital 科研 PDF；
- 默认 PDFium，region-render 默认关闭；
- 本地、非 AI、无云 OCR、无外部 API。

macOS、Windows ARM 和 Linux ARM 尚未验证。

## 获取源码

使用 Git：

```bash
git clone https://github.com/lljh2777-cyber/paperwright.git
cd paperwright
```

没有 Git 时，可在 GitHub 选择 **Code → Download ZIP**。解压后进入包含
`pyproject.toml`、`src/` 和 `README.md` 的目录。

## Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .

paperwright --version
paperwright --help
paperwright convert .\papers\a.pdf .\converted\a
paperwright batch .\converted-batch --input-dir .\papers --continue-on-error
paperwright validate-model .\converted\a\physical_document.json
```

如果 PowerShell 禁止激活脚本，可只为当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

也可以不激活，直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m paperwright --help
```

## Linux

如果 `venv` 或 `pip` 不可用，请先安装发行版提供的 `python3-venv` 与
`python3-pip`。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .

paperwright --version
paperwright --help
paperwright convert papers/a.pdf converted/a
paperwright batch converted-batch --input-dir papers --continue-on-error
paperwright validate-model converted/a/physical_document.json
```

## 显式文件列表

```bash
paperwright batch converted-batch \
  --input-file papers/a.pdf \
  --input-file papers/b.pdf
```

也可使用 UTF-8 文件清单：

```bash
paperwright batch converted-batch --file-list papers.txt
```

## auto region-render

默认关闭。需要时明确启用：

```bash
paperwright batch converted-auto \
  --input-dir papers \
  --region-render-mode auto \
  --region-render-max-candidates 12
```

## 批处理行为

- `--input-dir` 只扫描第一层 `.pdf`，从不递归；
- 输入确定性排序；
- 每个 PDF 写入 `0001-name/` 等独立目录；
- 输出目录已存在时拒绝覆盖；
- 文档失败不会留下半成品目录；
- 默认首个失败后停止；
- `--continue-on-error` 会继续，但最终仍返回非零退出状态；
- `batch_summary.json` 不记录绝对输入路径或凭据。

## 结果文件

单文档输出：

- `article.md`
- `images/`
- `manifest.json`
- `physical_document.json`

批处理额外生成 `batch_summary.json`。

## 卸载

安装位于项目虚拟环境中。退出虚拟环境：

```bash
deactivate
```

需要清理时，可由用户自行删除项目中的 `.venv` 目录；这不会删除输入 PDF 或
已经生成的输出目录。
