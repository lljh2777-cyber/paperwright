# Stage C 测试报告

## 最终结果

- 运行时间：见 `realworld/test_summary.json` 各命令的 UTC。
- 环境：Python 3.12.13；pypdfium2 5.3.0；PDFium
  145.0.7616.0；Pillow 12.2.0。
- 正式检查：7/7 通过，0 failure，0 skip。
- 单元测试：43/43。
- 自生成 PDF 内容断言：13/13。
- Stage C 持久机器摘要检查：12/12。

## 实际命令

```text
python -m unittest discover -s tests -v
python tools/generate_fixtures.py --check
python tools/run_stage_b_smoke.py
python tools/check_stage_c_summary.py
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

完整 argv、UTC、exit、stdout/stderr 路径、大小和 SHA-256 见
`realworld/test_summary.json`；小型原始输出见
`realworld/test_evidence/`。

## 内容级检查

测试不是用“exit 0/文件非空”替代质量判断。自生成 fixture 的 13 个断言
覆盖：

- 两页 PhysicalDocument 与 source hash；
- 标题和基本双栏列优先；
- WinAnsi `Café`；
- 表格 degraded 且不伪造 Markdown 网格；
- 嵌入 PNG 的格式、16×12 尺寸、24 色内容与 hash；
- manifest 元素覆盖；
- 两轮逐文件确定性。

Stage C 新增 7 个测试方法，覆盖：

- 逐词对象同行顺序；
- 20pt 窄栏 gutter 不被同行合并；
- 原双栏 fixture 不回归；
- 缺失元数据的多行几何标题；
- 错误通用元数据标题拒绝；
- C0 控制字符只从 Markdown 清理；
- 8 个冻结 OA URL 的 HTTPS host、CC BY、hash/页数政策，以及恶意/
  非权威 URL 拒绝。

## 安全回归

- 损坏 PDF：非零/异常，目标输出和原子临时目录不保留。
- 已存在输出目录：后端运行前拒绝覆盖。
- 输入/输出冲突、workspace escape：拒绝。
- repo policy：扫描 70 个 source-only 文件，0 违规；拒绝 PDF、图片、
  JAR/binary、缓存、凭据和大文件。

## 已披露的非最终失败

1. Stage C runner 首次空 `--paper` 选择判断错误；在任何 baseline backend
   子进程启动前失败，修复后才运行 8 篇。
2. 第一版窄栏回归测试揭示 55% wide-line 阈值会吞掉同高右栏；改为
   65%，最终 43/43 通过。
3. RW2-001 首次网络传输截断并被拒绝；有效 payload 为后续冻结 hash。
4. RW2-008 NCBI 历史 package 404；改用权威 Europe PMC PDF。

这些记录没有从最终摘要中隐藏，但交互期大日志不进入 source-only 包。
