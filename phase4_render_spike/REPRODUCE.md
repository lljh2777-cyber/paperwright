# Phase 4 region-render spike 复现

基线：`ee379a5be6c713012e721d08995a88d5abec19af`。

## 无真实论文 payload 的完整源码检查

```bash
PYTHONPATH=src:tests python tools/run_phase4_spike_checks.py
```

等价展开命令见根目录 `REPRODUCE.md`。所有 fixture PDF 都在临时目录
确定性生成，不提交仓库。

## 使用冻结 RW2 本地测试输入

先确保 `/isolated/RW2-pdfs/RW2-001.pdf` 至 `RW2-008.pdf` 与
`realworld/oa_sources.json` 的 SHA-256、字节数和页数严格一致：

```bash
PYTHONPATH=src python tools/run_phase4_render_spike.py \
  --repo . \
  --pdf-dir /isolated/RW2-pdfs \
  --output-root /isolated/phase4-render-spike

PYTHONPATH=src python tools/analyze_phase4_render_spike.py \
  --repo . \
  --runtime /isolated/phase4-render-spike \
  --phase3-runtime /isolated/phase3-output
```

脚本不会下载论文，也不会替换样本。PDF、裁剪图、接触图及完整转换输出均
留在隔离 runtime，不进入源码包。
