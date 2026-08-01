# Paper2MD 0.7 Alpha 架构

## 数据流

```text
CLI / Python API
       |
       v
PathPolicy + Config
       |
       v
Backend protocol ---- PDFBox（未实现的替换边界）
       |
       v
PDFium full / text-only / hybrid extraction
       |
       v
PhysicalDocument + optional raster evidence
       |
       +------------------------------+
       |                              |
       v                              v
Direct conversion               Hybrid layout review
Figure/Caption rules            Content ROI -> candidates
       |                        -> structured final layout
       v                              |
writer.write_outputs()                v
                              layout_writer.write_layout_outputs()
       |                              |
       +---------------+--------------+
                       v
           Markdown + images + manifest + provenance/evidence
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
- `paper2md.region_render`：默认不启用；`auto` 只规划同页、显式
  caption、有充分 bitmap/vector/text 几何证据且能证明 native 不完整的
  裁剪请求。跨页 continuation、近整页、caption 冲突/歧义、正文侵入和
  候选水平范围明显窄于 caption 等情形直接拒绝。
- `paper2md.writer`：把 PhysicalDocument 与内存资产确定性写入隔离临时
  目录，再原子提交。
- `paper2md.layout_candidates`：提出 Content ROI，生成文字、原生图形和栅格
  候选及分隔关系；不直接决定最终语义布局。
- `paper2md.raster_layout`：生成 ink/text/residual mask 和高召回视觉候选。
- `paper2md.layout_models`：定义布局任务、复核动作、最终区块及其严格契约。
- `paper2md.layout_review`：验证候选是否完整分配、动作是否可追溯、语义角色
  是否一致。
- `paper2md.layout_risk`：决定 `standard` 配置下哪些页面需要完整对象分析。
- `paper2md.layout_writer`：将已验证布局吸附到 PDF 对象，恢复文字、渲染视觉
  区块并生成自包含证据包。
- `paper2md.quality` 与 `paper2md.evidence`：区分启发式 warning 和确定性结构
  检查，生成可定位的验证报告。
- `paper2md.layout_dataset`：导出不含正文、页面图像和对象 ID 的数值训练数据。
- `paper2md.cli`：暴露直接转换、布局准备/校验/应用、数据导出和只读基准命令；
  错误转为明确非零退出状态。

## 提取配置

- `fast`：只读取 TextPage 文字与坐标，批量渲染低分辨率页面并生成栅格证据；
- `standard`：先执行 fast 风险评估，只对触发门槛的页面做完整对象遍历；
- `forensic`：对全部页面执行完整对象遍历，保留兼容行为。

准备阶段记录请求配置、实际配置、升级页和缓存哈希。应用阶段重放这些决定，
不允许在未声明的情况下换用另一种提取方式。

## PhysicalDocument 原则

- 坐标单位为 PDF point；
- 原点和轴方向必须逐页声明，MVP 固定为左上原点、y 向下；
- page index 从 0 开始且连续；
- 元素 ID 在文档内唯一；
- bbox 必须有限、正面积且位于页面范围；
- 后端不能提供的字段必须是 `null` 并给出 reason，禁止猜测；
- provenance 是每个元素的必需字段；
- 序列化使用 UTF-8、排序键、固定分隔符和 NFC 文本。

## 输出与契约兼容性

包版本和数据契约独立演进：

| 数据 | 当前版本 | 兼容说明 |
|---|---|---|
| Python 包 | `0.7.0a0` | Alpha 功能版本 |
| PhysicalDocument | v0.2 | 后端无关物理模型 |
| direct/off manifest | v0.4 | 保持旧默认输出 |
| direct region-render manifest | v0.5 | 增加 `region_render_policy` |
| hybrid manifest | v0.7 | 当前写出；继续接受旧 v0.6 |
| layout task | v0.1/v0.2 | v0.2 增加栅格证据 |
| final layout | v0.1 | 严格结构化复核结果 |
| layout provenance | v0.4 | 段落、caption、对象和修复追溯 |

`layout-apply` 默认生成自包含包，正文和图片位于顶层，运行信息、ROI、最终布局、
provenance 与验证报告位于 `_paper2md/`。`minimal`、`standard`、`full` 控制证据
保留范围，不改变论文正文的布局计划。

## 产品边界

直接转换的 auto region-render 仍只在 Figure/caption 周边调整局部 Markdown
放置，不宣称完整语义阅读顺序恢复。高置信同页 caption 配对时，Figure 放在 caption 之前；
歧义、无 caption 或跨页候选保持页末降级。多个原生位图可按 PDF bbox
组合；只有后端实际执行裁剪页面渲染后，
`vector_evidence.rendered_into_asset` 才能为 true。直接转换中的区域渲染是显式页面
白名单 opt-in，并保留原始 embedded/grouped 资产。表格继续只保留文字并
明确标为 `degraded`。

混合布局流程允许人工或视觉 AI 输出结构化区块计划，但 AI 不转录、改写正文，
也不读取 Figure/Table 内部文字。规则候选通过不代表最终语义布局正确；正式应用
前必须验证 `final-layout.json`。项目不实现 OCR、语义表格或公式 LaTeX。
PDFium 运行时不进入源码交付包；PDFBox 只是可替换接口。
