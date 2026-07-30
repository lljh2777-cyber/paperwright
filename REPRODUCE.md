# Paper2MD v2 复现说明

当前候选为 Phase 5 Alpha。Phase 4 的复现说明保留在下文；Alpha 的安装、
批处理、全量回归和 Windows 复测说明见
[`phase5_alpha/REPRODUCE.md`](phase5_alpha/REPRODUCE.md)。

Alpha 权威基线：
`5656eeff3d95ed7a3f025c5763bd94c5be565abe`。

## Phase 5 Alpha 快速验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src:tests python tools/run_phase5_batch_checks.py \
  --repo . --output-root /isolated/phase5-batch \
  --summary phase5_alpha/batch_test_summary.json
PYTHONPATH=src:tests python tools/run_phase5_install_checks.py \
  --repo . --output-root /isolated/phase5-install \
  --summary phase5_alpha/install_test_summary.json
python tools/check_phase5_summary.py
```

构建出的 wheel/sdist 只用于隔离安装验证，不进入 Git 或 source-only 交付包。

## Phase 4 auto region-render 复现说明

## 环境

- Python 3.10 或更高版本；
- `pypdfium2==5.3.0`（PDFium `145.0.7616.0`）；
- `Pillow==12.2.0`；
- 不需要 PDFBox、Java、OCR、LLM 或网络。

## 从源码运行

```bash
PYTHONPATH=src python -m paper2md --version
PYTHONPATH=src python -m paper2md convert input.pdf output-dir
PYTHONPATH=src python -m paper2md validate-model \
  tests/fixtures/physical_document.minimal.json
```

## 测试

```bash
python -m unittest discover -s tests -v
python tools/check_repo_policy.py --root .
PYTHONPATH=src:tests python tools/run_phase3_checks.py
PYTHONPATH=src python -m unittest tests.test_phase4_region_render -v
```

测试不以“进程退出码 0”作为唯一证据。单元测试分别断言：

- 模型字段与跨引用约束；
- bbox 正面积、页内坐标与唯一 ID；
- manifest 输出哈希和状态；
- JSON 序列化逐字节确定；
- CLI 错误分类和非零退出；
- 输出目录已存在、路径越界和输入/输出冲突被拒绝；
- 真实自生成 PDF 的标题、双栏顺序、图片、表格降级和元素追溯；
- 两轮完整输出逐文件哈希确定；
- 损坏 PDF 不留下半成品目录；
- PDFBox 未绑定时不会伪造转换结果；
- 仓库中不存在被禁止的大文件、PDF、二进制和常见凭据。

## fixture

`tests/fixtures/physical_document.minimal.json` 由
`tools/generate_fixtures.py` 确定性生成：

```bash
PYTHONPATH=src python tools/generate_fixtures.py --check
```

`--check` 只比较现场 fixture 与规范序列化结果，不覆盖文件。

born-digital PDF fixture 由 `tests/pdf_fixture_factory.py` 在临时目录中生成，
不会写入仓库或交付包。

## Phase 3 Figure/Caption 测试

冻结自生成标注位于 `phase3/fixtures/figure_caption_cases.json`，覆盖单图、
多面板、相邻两图、双栏 caption、歧义/无 caption、跨页拒配和局部顺序。

8 篇 OA PDF 不进入仓库。已有合法本地副本时，可执行：

```bash
PYTHONPATH=src:tests python tools/run_phase3_corpus.py \
  --pdf-root /isolated/oa-pdfs \
  --output-root /isolated/phase3-output \
  --sources realworld/oa_sources.json \
  --summary /isolated/phase3-run-summary.json

PYTHONPATH=src python tools/analyze_phase3_results.py \
  --output-root /isolated/phase3-output \
  --baseline-root /isolated/stage-c-output \
  --sources realworld/oa_sources.json \
  --run-summary /isolated/phase3-run-summary.json \
  --json-output /tmp/phase3_summary.json \
  --csv-output /tmp/figure_metrics.csv
```

脚本会逐件校验冻结 SHA-256、字节数和页数；不匹配时停止，不能换样本。

## 通用 auto region-render

先验证自生成 fixture 与全部回归：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
```

默认关闭，不改变既有输出：

```bash
PYTHONPATH=src python -m paper2md convert input.pdf output-off
```

显式 opt-in：

```bash
PYTHONPATH=src python -m paper2md convert input.pdf output-auto \
  --region-render-mode auto \
  --region-render-max-candidates 12
```

调试某页（页索引从 0 开始）：

```bash
PYTHONPATH=src python -m paper2md convert input.pdf output-explicit \
  --region-render-mode explicit \
  --region-render-page 2
```

真实 PDF 不进入仓库。若现场已有 `realworld/oa_sources.json` 中严格匹配的
8 份本地测试输入，可运行：

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

工具只对 RW2-005 页索引 2/6 与 RW2-007 页索引 4 启用裁剪渲染；默认
CLI/API 不会隐式启用 spike。

8 篇冻结 OA 本地副本存在时，可复算通用模式：

```bash
PYTHONPATH=src python tools/run_phase4_auto_corpus.py \
  --repo . \
  --pdf-dir /isolated/RW2-pdfs \
  --output-root /isolated/phase4-auto \
  --mode auto \
  --max-candidates 12

PYTHONPATH=src python tools/analyze_phase4_auto_results.py \
  --repo . \
  --default-runtime /isolated/default-current \
  --auto-runtime /isolated/phase4-auto \
  --baseline-root /isolated/baseline-25e4ece \
  --summary /tmp/auto_region_summary.json \
  --inventory-json /tmp/auto_candidate_inventory.json \
  --inventory-csv /tmp/auto_candidate_inventory.csv
```

真实 PDF、转换输出与视觉图不进入 source-only 包。来源必须与
`realworld/oa_sources.json` 的 SHA-256、字节数和页数完全一致。
