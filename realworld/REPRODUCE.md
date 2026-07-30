# Stage C 离线/受限联网复现

基线提交：`0897f3ca82b74468ece7aa65d6e331416c4afd96`。

## 1. 准备锁定环境

```bash
python -m pip install --no-deps pypdfium2==5.3.0 Pillow==12.2.0
export PYTHONPATH="$PWD/src:$PWD/tests"
```

实际验证环境为 Python 3.12.13、pypdfium2 5.3.0、PDFium
145.0.7616.0、Pillow 12.2.0。正式二进制分发仍未获批准；尤其
`agg23=NOASSERTION` 保持发布门禁。

## 2. 获取 OA 测试输入

下面命令只访问 `realworld/oa_sources.json` 中冻结的官方/权威 HTTPS
地址，并按大小和 SHA-256 拒绝漂移内容。目标目录必须不存在。

```bash
python tools/fetch_stage_c_oa.py \
  --output-root /tmp/paper2md-rw2-pdfs
```

PDF 只供本地测试，不进入 Git 或 source-only ZIP。

## 3. 运行 8 篇转换

```bash
python tools/run_stage_c_corpus.py \
  --pdf-root /tmp/paper2md-rw2-pdfs \
  --output-root /tmp/paper2md-rw2-final \
  --run-label stage-c-reproduce \
  --timeout 300
```

## 4. 运行 4 篇第二轮确定性检查

```bash
python tools/run_stage_c_corpus.py \
  --pdf-root /tmp/paper2md-rw2-pdfs \
  --output-root /tmp/paper2md-rw2-determinism \
  --run-label stage-c-reproduce-determinism \
  --paper RW2-001 --paper RW2-003 --paper RW2-005 --paper RW2-007 \
  --timeout 300
```

## 5. 重算真实样本摘要

`analyze_stage_c_realworld.py` 默认拒绝覆盖仓库内已有摘要。若需完整重算，
请在临时克隆中删除仅由该命令重建的
`realworld/realworld_summary.json`，然后运行：

```bash
python tools/analyze_stage_c_realworld.py \
  --pdf-root /tmp/paper2md-rw2-pdfs \
  --baseline-root /path/to/preserved-stage-c-baseline \
  --final-root /tmp/paper2md-rw2-final \
  --determinism-root /tmp/paper2md-rw2-determinism
```

baseline 是 Stage B 代码在相同冻结输入上的一次历史运行；source-only 包
不含该运行现场。

## 6. 无真实 PDF 的完整提交前测试

```bash
python -m unittest discover -s tests -v
python tools/generate_fixtures.py --check
python tools/run_stage_b_smoke.py
python tools/check_stage_c_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```
