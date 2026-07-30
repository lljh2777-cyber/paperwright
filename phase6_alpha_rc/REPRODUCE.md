# Paper2MD Phase 6 Alpha RC 复现

权威基线：
`47e31abb58d062e1da0ecf92a2a303afddaa39af`。

## 应用候选 patch

```bash
git checkout --detach 47e31abb58d062e1da0ecf92a2a303afddaa39af
git apply --check phase6-alpha-rc-changes.patch
git apply phase6-alpha-rc-changes.patch
```

## 源码与历史回归

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python tools/generate_fixtures.py --check
PYTHONPATH=src:tests python tools/run_stage_b_smoke.py
PYTHONPATH=src python tools/check_stage_c_summary.py
PYTHONPATH=src python tools/check_phase3_summary.py
PYTHONPATH=src python tools/check_phase4_spike_summary.py
PYTHONPATH=src python tools/check_phase4_auto_summary.py
python tools/check_phase5_summary.py
python tools/check_phase6_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

## 隔离 batch

```bash
PYTHONPATH=src:tests python tools/run_phase5_batch_checks.py \
  --repo . \
  --output-root /isolated/phase6-batch \
  --summary /isolated/phase6-batch-summary.json
```

## 临时 wheel/sdist 安装

```bash
PYTHONPATH=src:tests python tools/run_phase6_install_checks.py \
  --repo . \
  --output-root /isolated/phase6-install \
  --summary /isolated/phase6-install-summary.json
```

脚本在两个隔离 venv 中分别安装新构建的 wheel 和 sdist，并执行 version、
help、convert、batch、validate-model。临时构件、fixture 和输出不能复制
进 Git/source-only 包。

## Windows

Phase 5 的 Windows/Python 3.11.2 独立结果已保存在
`phase5_alpha/windows_validation*`。本轮云端只验证 Linux；本地应再次从
Phase 6 patch 做 fresh-tree 检查，但不能修改历史 Windows 证据。
