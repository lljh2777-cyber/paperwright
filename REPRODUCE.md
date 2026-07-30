# v2-bootstrap 复现说明

## 环境

- Python 3.10 或更高版本；
- bootstrap 测试仅使用 Python 标准库；
- 不需要 PDFium、PDFBox、Java、OCR 或网络。

## 从源码运行

```bash
PYTHONPATH=src python -m paper2md --version
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
- PDFium/PDFBox 未绑定时不会伪造转换结果；
- 仓库中不存在被禁止的大文件、PDF、二进制和常见凭据。

## fixture

`tests/fixtures/physical_document.minimal.json` 由
`tools/generate_fixtures.py` 确定性生成：

```bash
PYTHONPATH=src python tools/generate_fixtures.py --check
```

`--check` 只比较现场 fixture 与规范序列化结果，不覆盖文件。
