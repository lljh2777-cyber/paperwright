# Paper2MD v2 / Phase 3 复现说明

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
