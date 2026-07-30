# v2-mvp 复现说明

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
