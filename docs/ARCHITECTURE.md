# v2-mvp 架构

## 数据流

```text
CLI / Python API
       |
       v
PathPolicy + Config
       |
       v
Backend protocol
  |              |
PDFium         PDFBox
(thin adapter) (comparison/fallback interface)
       |
       v
PhysicalDocument
       |
       v
Page-local Figure/Caption rules
       |
       v
Deterministic writer
       |
       v
article.md + images/ + physical_document.json + manifest.json
```

## 模块边界

- `paper2md.models`：不可依赖具体 PDF 后端的物理文档模型。
- `paper2md.backends.base`：后端协议、能力描述和运行身份。
- `paper2md.backends.pdfium`：调用 pypdfium2 的薄适配器；PDF 解析、字体/
  图像解码均由 PDFium 完成，项目不重写底层解析器。
- `paper2md.backends.pdfbox`：PDFBox 对照/回退边界；不得把 Java 对象泄漏到
  核心模型。
- `paper2md.api`：输入路径验证、后端选择和输出事务边界。
- `paper2md.manifest`：稳定 manifest 构造与契约检查。
- `paper2md.figures`：仅使用同页文本 marker、bbox、邻近/包含关系构建
  Figure group 与 caption association；不做图像语义理解。
- `paper2md.region_render`：只规划同页、显式 caption、有充分
  bitmap/vector evidence 的裁剪请求；跨页 continuation、近整页和歧义
  候选直接拒绝。
- `paper2md.writer`：把 PhysicalDocument 与内存资产确定性写入隔离临时
  目录，再原子提交。
- `paper2md.cli`：面向用户的最小命令行；错误转为明确非零退出状态。

## PhysicalDocument 原则

- 坐标单位为 PDF point；
- 原点和轴方向必须逐页声明，MVP 固定为左上原点、y 向下；
- page index 从 0 开始且连续；
- 元素 ID 在文档内唯一；
- bbox 必须有限、正面积且位于页面范围；
- 后端不能提供的字段必须是 `null` 并给出 reason，禁止猜测；
- provenance 是每个元素的必需字段；
- 序列化使用 UTF-8、排序键、固定分隔符和 NFC 文本。

## MVP 边界

Phase 4 spike 仍只在 Figure/caption 周边调整局部 Markdown 放置，不宣称完整语义
阅读顺序恢复。高置信同页 caption 配对时，Figure 放在 caption 之前；
歧义、无 caption 或跨页候选保持页末降级。多个原生位图可按 PDF bbox
组合；只有后端实际执行裁剪页面渲染后，
`vector_evidence.rendered_into_asset` 才能为 true。区域渲染是显式页面
白名单 opt-in，并保留原始 embedded/grouped 资产。表格继续只保留文字并
明确标为 `degraded`。不实现 OCR、语义表格或公式 LaTeX。PDFium 运行时
不进入源码交付包；PDFBox 只是可替换接口。
