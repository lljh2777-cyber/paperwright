# Paper2MD Phase 5 Alpha 复现

权威基线：
`5656eeff3d95ed7a3f025c5763bd94c5be565abe`。

## 1. 源码测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python tools/generate_fixtures.py --check
PYTHONPATH=src:tests python tools/run_stage_b_smoke.py
PYTHONPATH=src python tools/check_stage_c_summary.py
PYTHONPATH=src python tools/check_phase3_summary.py
PYTHONPATH=src python tools/check_phase4_spike_summary.py
PYTHONPATH=src python tools/check_phase4_auto_summary.py
python tools/check_phase5_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

## 2. Batch 机器验收

```bash
PYTHONPATH=src:tests python tools/run_phase5_batch_checks.py \
  --repo . \
  --output-root /isolated/phase5-batch-checks \
  --summary phase5_alpha/batch_test_summary.json
```

运行目录包含自生成临时 PDF 和转换结果，不进入 Git/source-only 包。

## 3. wheel/sdist 隔离安装

```bash
PYTHONPATH=src:tests python tools/run_phase5_install_checks.py \
  --repo . \
  --output-root /isolated/phase5-install-checks \
  --summary phase5_alpha/install_test_summary.json
```

该脚本在临时源码副本中构建 wheel/sdist，分别安装到新 venv，并实际调用：

```text
paper2md --version
paper2md --help
paper2md convert
paper2md batch
paper2md validate-model
```

Work 环境没有离线依赖 wheel 仓库，因此 venv 使用
`system_site_packages=True` 读取预先核验的 pypdfium2/Pillow；Paper2MD
本身仍从新构建的 wheel/sdist 标准安装，且没有联网。此事实不能表述为完全
自包含的离线安装器。

## 4. 本地 Windows 门禁建议

```powershell
git apply --check .\phase5-alpha-changes.patch
git apply .\phase5-alpha-changes.patch
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
python -m unittest discover -s tests -v
paper2md --version
paper2md --help
```

随后按 `docs/QUICKSTART_ALPHA.md` 复测 convert/batch/validate-model。云端报告
不声称 Windows 已验证。

## 5. 真实论文回归

本阶段复用提交中
`phase4_auto_region/auto_region_summary.json` 的 8/8 默认关闭逐文件兼容
结论，不下载论文，也不重建大型输出。
