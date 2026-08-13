# Paper2MD Alpha 复现说明

## 环境

- Python 3.10–3.13；
- `pypdfium2==5.11.0`；
- `Pillow==12.2.0`；
- 不需要 Java、OCR、LLM 或网络服务。

## 测试

PowerShell：

```powershell
$env:PYTHONPATH = "src"
D:\python\python.exe -m unittest discover -s tests -v
D:\python\python.exe tools\generate_fixtures.py --check
D:\python\python.exe -m compileall -q src tests tools
D:\python\python.exe tools\check_repo_policy.py --root .
git diff --check
```

Linux/macOS shell：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python tools/generate_fixtures.py --check
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

## 内容 smoke

```powershell
$env:PYTHONPATH = "src;tests"
D:\python\python.exe tools\run_content_smoke.py
```

该检查使用运行时生成的 born-digital PDF，验证标题、双栏顺序、Unicode、
图片、表格降级、manifest 追溯和双轮逐文件确定性。生成的 PDF 与输出不会
写入仓库。

## Batch 检查

在仓库外准备一个空运行目录：

```powershell
$env:PYTHONPATH = "src;tests"
D:\python\python.exe tools\run_batch_checks.py `
  --repo . `
  --output-root C:\Temp\paper2md-batch-check `
  --summary C:\Temp\paper2md-batch-summary.json
```

## wheel/sdist 安装检查

```powershell
$env:PYTHONPATH = "src;tests"
D:\python\python.exe tools\run_install_checks.py `
  --repo . `
  --output-root C:\Temp\paper2md-install-check `
  --summary C:\Temp\paper2md-install-summary.json
```

构建出的 wheel/sdist 仅用于隔离安装验证，不进入 Git。安装检查会实际运行
`--version`、`--help`、`convert`、`batch` 和 `validate-model`，并比较
wheel 与 sdist 的内容输出。

## 安全边界

- 不覆盖已有输出；
- 不允许输入/输出目录危险嵌套；
- batch 不默认递归扫描，也不跟随越界链接；
- 损坏 PDF 不留下伪成功或半成品目录；
- PDFBox 未实现时明确失败；
- region-render 默认关闭，`auto` 必须显式启用。

完整的阶段研发证据和历史复现脚本位于 `agent/v2-rebuild` 分支。
