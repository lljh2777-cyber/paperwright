# Phase 4 通用 auto region-render 复现

权威基线：`25e4ecea02979cf7dcb56ab2d280425bc56e74e2`。

## 无真实论文的完整测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python tools/generate_fixtures.py --check
PYTHONPATH=src:tests python tools/run_stage_b_smoke.py
PYTHONPATH=src python tools/check_stage_c_summary.py
PYTHONPATH=src python tools/check_phase3_summary.py
PYTHONPATH=src python tools/check_phase4_spike_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

## 8 篇冻结 OA 输入

以下命令只允许使用与 `realworld/oa_sources.json` 的 SHA-256、字节数和页数
全部匹配的本地副本；脚本不下载、替换或回写 PDF：

```bash
PYTHONPATH=src python tools/run_phase4_auto_corpus.py \
  --repo . \
  --pdf-dir /isolated/RW2-pdfs \
  --output-root /isolated/phase4-auto-v2 \
  --mode auto \
  --max-candidates 12

PYTHONPATH=src python tools/analyze_phase4_auto_results.py \
  --repo . \
  --default-runtime /isolated/default-current \
  --auto-runtime /isolated/phase4-auto-v2 \
  --baseline-root /isolated/baseline-25e4ece \
  --summary phase4_auto_region/auto_region_summary.json \
  --inventory-json phase4_auto_region/auto_candidate_inventory.json \
  --inventory-csv phase4_auto_region/auto_candidate_inventory.csv
```

默认关闭的回归需另以 `--mode off` 运行，并与基线输出树逐文件比较。

视觉证据构建脚本只从已核验 PDF 与已有最终输出派生 PNG，不执行转换：

```bash
PYTHONPATH=src python tools/build_phase4_auto_visual_evidence.py \
  --repo . \
  --pdf-dir /isolated/RW2-pdfs \
  --auto-root /isolated/phase4-auto-v2/first \
  --output /isolated/phase4-auto-visual
```

PDF、真实转换输出和视觉 PNG 均不得进入 source-only ZIP 或 Git。
