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

MVP 只对原生文本对象做确定性几何排序，支持简单双栏，不宣称语义阅读
顺序恢复。图片按原生 image object 提取并放在对应页末，不推断 caption
邻接。表格只保留文字并明确标为 `degraded`。不实现 OCR、语义表格、
公式 LaTeX 或完整矢量 Figure 重建。PDFium 运行时不进入源码交付包；
PDFBox 只是可替换接口。
