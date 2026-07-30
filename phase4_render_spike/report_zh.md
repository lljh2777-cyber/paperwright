# Paper2MD Phase 4 region-render spike 总报告

正式结论：`PHASE4_RENDER_SPIKE_PASS`。

交付打包器首次执行因将 `hard_checks` 映射误当作列表而中止（exit 1）；
该失败未触及产品或运行证据，已保存在 `packaging_attempts.json`，修正后才生成正式交付包。

该结论只表示三个冻结目标证明了 PDFium clipped page-region render
可以补齐 Phase 3 的混合位图/矢量 Figure，并且没有核心回归；不表示完整
Phase 4、Alpha、发布打包或二进制分发已放行。

## 基线与范围

- 权威基线：`ee379a5be6c713012e721d08995a88d5abec19af`；
- 运行时：pypdfium2 5.3.0 / PDFium 145.0.7616.0 / Pillow 12.2.0；
- 输入：`realworld/oa_sources.json` 的同一 8 篇 OA PDF，138 页；
- 冻结标注在任何候选裁剪生成前落盘；
- 无 LLM/API、OCR、视觉模型、云服务或新论文。

## 三个目标

### A. RW2-005 第 3 页 Figure 1

通过必要门槛。bbox 直接取 PDF 原生外框
`p0002-vector-00015`：
`x=36.377998, y=53.875, width=532.244987, height=441.264008 pt`。
PDFium 以 2×/144 DPI 生成单个 1064×882 PNG：

`3e239449a19512b82fc4199bf6f8229f536f09b54deaa1acaa5c98b004639abf`

人工并排检查确认 a–d、四块显微位图、b/d 矢量散点图、坐标轴、标题和
外框完整；Phase 3 基线缺少这些矢量内容。裁剪不含 caption、正文、页眉
或页脚，页面面积比 0.484544。Markdown 中图片紧邻 Figure 1 caption。

### B. RW2-005 第 7 页 Figure 3

按设计安全拒绝。原页明确写有 “Figure 3 continued on next page”，而
spike 禁止跨页聚合。最终没有 region-rendered 资产，manifest 记录
`cross_page_figure_continuation_explicitly_detected`，维持 degraded。

### C. RW2-007 第 5 页 Figure 2

适用且通过。Phase 3 单一原生 bitmap 在 y=444.655 结束，图内 vector/text
继续到 y=589.115。确定性同页 evidence 并集加 4 pt padding 得到
`x=83.612, y=47.784119, width=428.036011, height=545.330460 pt`。
2×/144 DPI 输出 855×1090 PNG：

`e23fd761347557227666093f4ce78d0821d9a5a9cae4f3039d94ca0c80a8729a`

人工检查确认 a–e 全部可见，尤其原生 bitmap 缺失的 e 条形图与图内矢量
文字真实进入裁剪；不含页眉、caption、页脚或正文。

## 回归、确定性与统计

- 8/8 默认回归、138 页；
- 回归现场 125 文件、152,609,856 bytes；
- 8/8 PhysicalDocument、article.md、images 相对 Phase 3 run1 哈希一致；
- 目标 4 个文档运行（两个 backend-independent target 文档各两轮），
  共 122 文件、183,972,900 bytes；
- 三个目标观测各两轮；RW2-005、RW2-007 两棵输出树均逐文件相同；
- 每轮 2 个 region-rendered asset、1 个安全拒绝；
- conversion failure/timeout/skip = 0/0/0；
- 60/60 单元测试，9/9 顶层检查。

第一次 runtime 暴露了 vector evidence 数量/哈希口径不一致；已保留 v1
证据并以最小代码修复后在 v2 完整重跑。两版裁剪 PNG 哈希不变。

## 防护与 manifest

只有明确白名单页面可启用 fallback。PDFium 直接执行 `page.render(crop=...)`，
不是内部对象拼接。硬拒绝包括越界、caption guard、近整页、零尺寸、像素
上限、空白/恒定图和跨页 continuation。manifest v0.4 明确区分
`embedded`、`grouped`、`region-rendered`，同时保留 native asset、成员、
source object、vector evidence、bbox 规则、renderer 身份和源 PDF 哈希。

## 限制

- 当前仍是三目标 opt-in spike，不默认扩展到所有 Figure；
- bbox 推导只覆盖原生外框或同页 image/vector/text 到明确 caption 的证据；
- 不处理 OCR、扫描 PDF、语义表格、公式 LaTeX、跨页 Figure 或深层图像语义；
- `agg23=NOASSERTION` 继续只锁定正式二进制分发，不阻断 source-only 研发；
- 8 篇样本不能外推为全部出版商版式。

下一步若由本地审计放行，应先把同样的拒绝规则扩展到更大的冻结 Figure
集合，再考虑完整 Phase 4；本阶段不自动进入。
