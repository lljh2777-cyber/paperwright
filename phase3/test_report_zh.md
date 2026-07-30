# Phase 3 测试报告

结论：源代码检查 8/8 通过，0 failure，0 skip。

- 单元测试：48/48；其中 Stage C 既有 43/43，Phase 3 新增 5 个测试
  方法，数据驱动覆盖 8 个冻结 Figure/Caption case。
- MVP 内容断言：13/13。
- Stage C 持久摘要检查通过，确认旧小型证据未被改写。
- Phase 3 持久摘要 17/17。
- fixture check、compileall、repo policy、`git diff --check` 均通过。
- 安全/路径回归仍覆盖损坏 PDF、已有输出目录、workspace escape 和
  输入/输出冲突；产品输出继续使用原子临时目录。

真实运行：

- 8 篇均运行一次，4 篇额外运行一次；
- 12 次转换、227 个现场文件、350,030,083 bytes；
- run1 权威口径为 125 文件、152,588,151 bytes；
- failure / timeout / skip = 0 / 0 / 0；
- 4/4 双轮逐文件哈希一致；
- 41 个 Figure 资产均非空且不是恒定像素图，输出引用/hash/尺寸一致。

机器台账见 `test_summary.json`；真实结果见 `phase3_summary.json`；测试
日志为小型文本文件，位于 `phase3/test_evidence/`。
