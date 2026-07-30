# Phase 4 region-render spike 测试报告

结论：9/9 顶层检查通过，0 failure，0 skip。

- 旧测试：48/48；
- 新增 region-render 测试：12/12；
- 合计：60/60；
- 自生成 fixture 覆盖坐标裁剪、rotation、scale/DPI、越界、近整页、
  空白/恒定图、像素上限、caption guard、两轮确定性、跨页拒绝、
  manifest 与默认关闭；
- fixture check、内容 smoke、Stage C 摘要、Phase 3 摘要、Phase 4
  持久摘要、compileall、repo policy、diff check 均通过。

锁定环境：

- Python 3.12.13；
- pypdfium2 5.3.0；
- PDFium 145.0.7616.0；
- Pillow 12.2.0。

真实回归使用 8 篇冻结 OA PDF，138 页。默认关闭 region render 时，
8/8 PhysicalDocument、article.md 与全部 images 相对 Phase 3 run1
逐文件哈希一致。真实目标两轮输出逐文件哈希一致。

详细命令、UTC、stdout/stderr 大小和哈希见 `test_summary.json` 与
`test_evidence/`。测试没有把 exit 0、文件存在或非空单独视作质量通过。
