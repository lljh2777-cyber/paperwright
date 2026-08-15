# PaperWright 0.9 Alpha 架构

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
       v                              v
Markdown + images + manifest     article model
                                      |
                                      v
                         Markdown + reader + manifest + images/evidence
```

## 模块边界

- `paperwright.models`：不可依赖具体 PDF 后端的物理文档模型。
- `paperwright.backends.base`：后端协议、能力描述和运行身份。
- `paperwright.backends.pdfium`：调用 pypdfium2 的薄适配器；PDF 解析、字体/
  图像解码均由 PDFium 完成，项目不重写底层解析器。
- `paperwright.backends.pdfbox`：PDFBox 对照/回退边界；不得把 Java 对象泄漏到
  核心模型。
- `paperwright.api`：输入路径验证、后端选择和输出事务边界。
- `paperwright.manifest`：稳定 manifest 构造与契约检查。
- `paperwright.figures`：仅使用同页文本 marker、bbox、邻近/包含关系构建
  Figure group 与 caption association；不做图像语义理解。
- `paperwright.region_render`：默认不启用；`auto` 只规划同页、显式
  caption、有充分 bitmap/vector/text 几何证据且能证明 native 不完整的
  裁剪请求。跨页 continuation、近整页、caption 冲突/歧义、正文侵入和
  候选水平范围明显窄于 caption 等情形直接拒绝。
- `paperwright.content_render`：与 region-render 同开关；保守识别同页
  表格（显式 Table caption + 列对齐原生行）和独立公式（居中、短行、
  垂直隔离、含数学记号），失败时保留原生文字。
- `paperwright.writer`：把 PhysicalDocument 与内存资产确定性写入隔离临时
  目录，再原子提交。
- `paperwright.layout_candidates`：提出 Content ROI，生成内部使用的文字、原生图形和
  栅格风险证据；不直接决定最终语义布局。默认 `visual-direct` 审核不会把这些
  规则候选坐标交给视觉审核者。
- `paperwright.layout_candidate_features`：只负责候选区块的几何、文字规律、字体、
  图形覆盖和少量模式特征，避免候选分割与特征计算互相耦合。
- `paperwright.raster_layout`：生成 ink/text/residual mask 和高召回视觉候选。
- `paperwright.layout_models`：定义布局任务、复核动作、最终区块及其严格契约。
- `paperwright.layout_review`：验证候选是否完整分配、动作是否可追溯、语义角色
  是否一致。
- `paperwright.layout_risk`：决定 `standard` 配置下哪些页面需要完整对象分析。
- `paperwright.layout_writer`：将已验证布局吸附到 PDF 对象，恢复文字、渲染视觉
  区块并生成自包含证据包。
- `paperwright.layout_caption`：在已验证区块之间执行确定性的 Figure/Table 与
  caption 几何绑定；不参与 Markdown 写出和区域渲染。
- `paperwright.layout_continuation`：集中管理同页正文、跨页正文和已绑定 caption
  的保守续接条件及 provenance 事件。
- `paperwright.reader`：把混合布局写出器的内部 trace 编译为公开 Markdown 锚点，
  生成正文块、视觉资产和图注关系索引。
- `paperwright.article_model`：保存复核后的规范文章块、内联 Markdown、source span、
  视觉资产和关系；严格验证后确定性投影为 `article.md` 与 `reader.json`。
- `paperwright.text_review`：从 Article Model 投影不含页面图像和几何来源的文本任务，
  校验带任务/模型哈希的受约束 Markdown 操作，并在保持身份图不变时生成新模型。
- `paperwright.synthesize`：L3 程序合成内核——受限 DSL（ast 白名单、只读原语、
  跨平台执行预算）、join 候选、声明式守恒校验，并构造与 L1 完全相同的
  `text-review.json`；另含 synthesis-run v0.1 溯源契约与确定性重放校验。
  模型调用与自修复留在 `tools/run_text_synthesize.py`。
- `paperwright.text_package`：验证完整 v0.9 源包，应用文本复核，并原子写出带父包、
  task、review 与验证报告哈希链的 manifest v0.10 派生包。
- `paperwright.reader_contract`：集中定义稳定 ID、可见文本指纹和 Reader 严格校验，
  交叉检查锚点、关系、路径、文件大小与哈希。
- `paperwright.llm_cost`：记录可选 bridge 的模型用量与估算成本；核心包不依赖
  任何 LLM SDK，usage 只是普通数据结构。
- `paperwright.quality` 与 `paperwright.evidence`：区分启发式 warning 和确定性结构
  检查，生成可定位的验证报告。
- `paperwright.layout_dataset`：导出不含正文、页面图像和对象 ID 的数值训练数据。
- `paperwright.cli`：暴露直接转换、布局准备/校验/应用、数据导出和只读基准命令；
  错误转为明确非零退出状态。

## 提取配置

- `fast`：只读取 TextPage 文字与坐标，批量渲染低分辨率页面并生成栅格证据；
- `standard`：先执行 fast 风险评估，只对触发门槛的页面做完整对象遍历；
- `forensic`：对全部页面执行完整对象遍历，保留兼容行为。

准备阶段记录请求配置、实际配置、升级页和缓存哈希。应用阶段重放这些决定，
不允许在未声明的情况下换用另一种提取方式。

`standard` 使用版本化的 `paperwright-layout-risk-v0.2` 策略。原生文字缺失、没有
候选或候选/栅格/分隔关系达到极端数量时直接升级；中等复杂页面则综合候选碎片、
栅格区域、分隔密度、混合内容、ROI 边界相交、栅格覆盖文字和外围内容比例。
只有多个独立信号共同达到阈值才升级。`review-index.json` 保存策略参数、分项指标、
风险分数、信号和最终原因，便于复现与后续用真实复核数据校准。

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
| Python 包 | `0.9.0a0` | Alpha 功能版本 |
| PhysicalDocument | v0.2 | 后端无关物理模型 |
| direct/off manifest | v0.4 | 保持旧默认输出 |
| direct region-render manifest | v0.5 | 增加 `region_render_policy` |
| hybrid manifest | v0.9 | 当前写出；继续接受旧 v0.6–v0.8 |
| text-reviewed manifest | v0.10 | 从完整 v0.9 包确定性派生，不覆盖父包 |
| text-synthesized manifest | v0.11 | v0.10 + L3 synthesis-run 溯源与重放哈希链 |
| article model | v0.1 | Markdown、Reader 与后续文本复核的规范来源 |
| text task | v0.2 | 只读文本块、编辑策略及源 Article Model 哈希；继续读 v0.1 |
| text review | v0.2 | 格式保持、断行去连字符与 join-blocks；继续读 v0.1 |
| reader index | v0.1 | 正文块、视觉资产、图注关系和能力声明 |
| Markdown anchor | v0.1 | `pwwd:block` / `pwwd:slot` 公共隐藏锚点 |
| layout task | v0.1/v0.2 | v0.2 增加栅格证据 |
| final layout | v0.1 | 严格结构化复核结果 |
| layout provenance | v0.5 | 增加 reader block/asset 反向引用 |

`layout-apply` 默认生成自包含包，正文和图片位于顶层，Article Model、Reader
索引、运行信息、ROI、最终布局、provenance 与验证报告位于 `_paperwright/`。
Article Model 与 Reader 在所有证据级别都保留；`minimal`、`standard`、`full`
只控制审计证据范围，不改变论文正文的布局计划。

Reader ID 由源 PDF 哈希和规范化源区域生成，不依赖 Markdown 行号、数组下标、
标题 slug、图片文件名或图注全文。正文块另带可见文本指纹，供用户编辑后做
显式重定位；指纹是修复线索，不会覆盖 ID 或静默接受错误绑定。

Article Model 复用同一稳定身份，按连续 `order` 保存每个公开块的单行 Markdown
和 source spans。公开 Markdown 锚点、可见文字指纹、article 哈希和 Reader 图均
从该模型重新计算；验证器拒绝模型、Markdown、Reader 或图片资产之间的漂移。

文本复核不会把 Article Model 交给模型任意改写。`text-task.json` 只包含块 ID、类型、
顺序、单行 Markdown 及内容哈希，不含页面图像、source span、资产或关系。v0.2
复核允许替换可编辑块的 Markdown（`format-only` 必须保持规范化可见文本相同，
`dehyphenation` 只能删除词内断行连字符及其后空白），并允许同页相邻 body 的
`join-blocks` 纯拼接；v0.1 仍可读取但不含 join-blocks。应用阶段再次绑定源模型与任务
哈希，并拒绝视觉槽位、身份、顺序或图关系变化。

`text-package` 将通过复核的新模型重新投影为 Markdown 与 Reader，同时复制并校验
父包的视觉资产和证据文件。新 manifest 绑定父 manifest、源模型、task、review、
验证报告及完整输出清单；临时目录通过全包校验后才会原子成为目标目录。

## 产品边界

直接转换的 auto region-render 仍只在 Figure/caption 周边调整局部 Markdown
放置，不宣称完整语义阅读顺序恢复。高置信同页 caption 配对时，Figure 放在 caption 之前；
歧义、无 caption 或跨页候选保持页末降级。多个原生位图可按 PDF bbox
组合；只有后端实际执行裁剪页面渲染后，
`vector_evidence.rendered_into_asset` 才能为 true。直接转换中的区域渲染是显式页面
白名单 opt-in，并保留原始 embedded/grouped 资产。表格继续只保留文字并
明确标为 `degraded`。

混合布局流程默认由人工或视觉 AI 直接依据原页图像输出结构化区块计划。审核者用
`add` 和规范化 bbox 绘制最终区块；PaperWright 再按几何覆盖把原生 PDF 元素归属到
区块。确认后的 Content ROI 是粗粒度语义内容边界：非排除区块不得越界，元素回接
也会过滤 ROI 外围内容；页眉、页脚等仍可作为 `exclude` 区块保留审计来源。ROI
包含标题、作者、脚注、Figure/Table 和 caption，不等同于狭义段落正文。
`candidate-assisted` 仅作为显式兼容模式保留。AI 不转录、改写正文，
也不读取 Figure/Table 内部文字。规则候选通过不代表最终语义布局正确；正式应用
前必须验证 `final-layout.json`。项目不实现 OCR、语义表格或公式 LaTeX。
PDFium 运行时不进入源码交付包；PDFBox 只是可替换接口。
