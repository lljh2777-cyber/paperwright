# v2-bootstrap 架构

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
(planned)      (comparison/fallback)
       |
       v
PhysicalDocument
       |
       v
后续阶段：Markdown AST / assets / manifest
```

## 模块边界

- `paper2md.models`：不可依赖具体 PDF 后端的物理文档模型。
- `paper2md.backends.base`：后端协议、能力描述和运行身份。
- `paper2md.backends.pdfium`：后续 PDFium 薄适配器入口；bootstrap 不加载
  或下载 PDFium。
- `paper2md.backends.pdfbox`：PDFBox 对照/回退边界；不得把 Java 对象泄漏到
  核心模型。
- `paper2md.api`：输入路径验证、后端选择和输出事务边界。
- `paper2md.manifest`：稳定 manifest 构造与契约检查。
- `paper2md.cli`：面向用户的最小命令行；错误转为明确非零退出状态。

## PhysicalDocument 原则

- 坐标单位为 PDF point；
- 原点和轴方向必须逐页声明，bootstrap 固定为左上原点、y 向下；
- page index 从 0 开始且连续；
- 元素 ID 在文档内唯一；
- bbox 必须有限、正面积且位于页面范围；
- 后端不能提供的字段必须是 `null` 并给出 reason，禁止猜测；
- provenance 是每个元素的必需字段；
- 序列化使用 UTF-8、排序键、固定分隔符和 NFC 文本。

## 后续阶段边界

bootstrap 不实现 PDF 解析、Markdown、阅读顺序、Figure/Caption、OCR、
表格语义或公式 LaTeX。PDFium 是计划主后端而不是预打包二进制；PDFBox
只是可替换接口。
