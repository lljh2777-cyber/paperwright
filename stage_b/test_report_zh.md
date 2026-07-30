# Paper2MD v2-mvp（Stage B）测试报告

## 结论

当前正式测试为 **36/36 通过，0 failure，0 skip**。fixture 契约检查、
内容级转换 smoke、CLI 版本、Python 编译、diff whitespace 与仓库存储政策
共 7 个顶层检查 7/7 通过。内容 smoke 另含 13 个独立断言，13/13 通过。

这不是旧 Phase 1B/Phase 2 检查点恢复，也不是对真实出版商论文的质量结论。

## 实际环境

- CPython 3.12.13，Linux x86_64；
- pypdfium2 5.3.0；
- PDFium 145.0.7616.0；
- `libpdfium.so` SHA-256
  `504df0960b4fab9e7c3bce8e4cf944d072a5aba76a5a199609d7addc49656568`；
- Pillow 12.2.0；
- PDFBox 未运行；网络、OCR、LLM/API 均未使用。

精确 UTC、argv、stdout/stderr 路径、大小、SHA-256 与 exit code 位于
`stage_b/test_summary.json`。

## 产品级断言

临时生成的两页 born-digital PDF（1,915 bytes）覆盖标题、WinAnsi Unicode
`Café`、有线表格、嵌入 RGB 位图和故意交错写入的双栏文本对象。验证结果：

- 输入 SHA 与 manifest 一致，PhysicalDocument 为 2 页；
- 标题输出为一级 Markdown 标题；
- Markdown 中顺序为 LEFT-ONE、LEFT-TWO、RIGHT-ONE、RIGHT-TWO；
- 20/20 PhysicalDocument 元素均有 manifest 追溯记录；
- 1 个原生 image object 输出为 16×12 PNG，24 种颜色，文件哈希匹配；
- 表格只保留文字并有 1 条 `table_structure_degraded`，未伪造 Markdown 行列；
- 两次隔离转换的 article、image、manifest、PhysicalDocument 四类文件逐哈希一致；
- 损坏 PDF 不留下目标目录或临时半成品；
- 已有输出、workspace 越界和输入/输出冲突继续拒绝。

## 视觉检查

独立使用 `pdftoppm -png -r 110` 渲染两页测试 PDF，并人工查看两页及提取
PNG。观察到标题、表格边框/单元格文字、渐变嵌入图和左右两栏均与 fixture
规格一致；提取图不是空白图或整页截图。精确哈希和观察记录见
`stage_b/visual_review_zh.md`。渲染图与运行输出只留在云端临时目录，不进入
Git 候选或交付 ZIP。

## 限制

- 仅验证项目自生成的简单 born-digital PDF，真实论文泛化尚未验证；
- 双栏仅为可解释的基础几何规则，不是完整阅读顺序恢复；
- 图片按页面末尾放置，未实现 caption 邻接或矢量 Figure 重建；
- 表格只做诚实降级，不恢复语义结构；
- 不处理扫描 PDF/OCR、公式 LaTeX、加密 PDF；
- 旋转页、复杂字体/CMap、链接/注释与资源限制仍需后续真实材料验证；
- `agg23=NOASSERTION` 继续阻断正式二进制分发批准，但不阻断本地开发与
  source-only 交付。
