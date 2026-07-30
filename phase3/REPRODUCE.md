# Phase 3 复现命令

基线：`8ecd01871eff02e700f0cef1c64cae186be8c69f`。

```bash
export PYTHONPATH=src:tests
python -m unittest discover -s tests -v
python tools/generate_fixtures.py --check
python tools/run_stage_b_smoke.py
python tools/check_stage_c_summary.py
python tools/check_phase3_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

若本地另有按 `realworld/oa_sources.json` 合法取得的 8 份 PDF：

```bash
export PYTHONPATH=src:tests
python tools/run_phase3_corpus.py \
  --pdf-root /ABSOLUTE/READ_ONLY/OA_PDFS \
  --output-root /ABSOLUTE/NEW/PHASE3_OUTPUT \
  --sources realworld/oa_sources.json \
  --summary /ABSOLUTE/NEW/phase3-run-summary.json
```

脚本要求输出根目录不存在，并逐件核验 PDF SHA-256、字节数和页数。
PDF、真实论文图片与转换输出均不得复制进仓库或 source-only 包。
