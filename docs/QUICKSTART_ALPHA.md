# Paper2MD Alpha 快速开始

## 支持范围

- Python 3.10–3.13；
- born-digital 科研 PDF；
- 默认 PDFium，region render 默认关闭；
- 本地、非 AI、无云 OCR、无外部 API。

安装前请自行准备与 `pyproject.toml` 完全一致的
`pypdfium2==5.3.0`、`Pillow==12.2.0`。源码包不捆绑 PDFium binary。

## Linux shell

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

paper2md --version
paper2md --help
paper2md convert paper.pdf converted-paper
paper2md batch converted-batch --input-dir papers --continue-on-error
paper2md validate-model converted-paper/physical_document.json
```

显式文件：

```bash
paper2md batch converted-batch \
  --input-file papers/a.pdf \
  --input-file papers/b.pdf
```

auto region-render 必须明确启用：

```bash
paper2md batch converted-auto \
  --input-dir papers \
  --region-render-mode auto \
  --region-render-max-candidates 12
```

## Windows PowerShell

以下命令已在 Phase 5 的 Windows/Python 3.11.2 本地门禁中独立通过；
Phase 6 云端只重新验证 Linux，不能把本轮表述为再次验证 Windows。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .

paper2md --version
paper2md --help
paper2md convert .\papers\a.pdf .\converted\a
paper2md batch .\converted-batch --input-dir .\papers --continue-on-error
paper2md validate-model .\converted\a\physical_document.json
```

## 批处理行为

- `--input-dir` 只扫描第一层 `.pdf`，从不递归；
- 也可重复 `--input-file`，或使用 UTF-8 `--file-list`；
- 输入按文件名大小写折叠值、原文件名和路径确定性排序；
- 每个 PDF 写入 `0001-name/` 等独立目录；
- 输出目录已存在时拒绝覆盖；
- 文档失败不会留下半成品目录；
- 默认首个失败后停止；`--continue-on-error` 会继续，但最终仍非零退出；
- `batch_summary.json` 不记录绝对输入路径或凭据。

## 结果文件

单文档输出：

- `article.md`
- `images/`
- `manifest.json`
- `physical_document.json`

批处理额外生成 `batch_summary.json`。其 `runtime` 字段不参与
`deterministic_content_sha256`。
