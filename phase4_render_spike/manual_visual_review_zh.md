# Phase 4 region-render spike 人工视觉复核

复核方式是并排打开原 PDF 全页渲染、Phase 3 原生/组合资产与 Phase 4
PDFium 裁剪资产。接触图仅保存在云端 runtime，不进入 source-only 包；
本文件保留输入与输出哈希、可见内容差异和判断。

## RW2-005 第 3 页 Figure 1

结论：`PASS_COMPLETE_REGION_RENDER`。

Phase 3 基线只包含四块显微位图，大块白区对应未渲染的 panel 标签、标题、
b/d 散点图、坐标轴和外框。最终单个 region asset 可见 a–d 四个 panel、
四块显微位图、两张矢量散点图、全部坐标轴/标题及原生外框。裁剪底边与
外框一致，没有 caption、正文、页眉或页脚；页面面积比 0.4845，不是整页
截图。Markdown 中 asset 位于 Figure 1 caption 紧前。

## RW2-005 第 7 页 Figure 3

结论：`PASS_SAFE_REJECTION`。

原页的 a–h panel 边界虽然清楚，但 caption 区明确出现
“Figure 3 continued on next page”。Phase 3 的三个资产也是不同局部碎片。
本 spike 禁止跨页聚合，故没有生成 region asset；manifest 以
`cross_page_figure_continuation_explicitly_detected` 保留 degraded。该拒绝
比强行把本页裁剪冒充完整 Figure 更安全。

## RW2-007 第 5 页 Figure 2

结论：`PASS_COMPLETE_REGION_RENDER`。

Phase 3 单一 native bitmap 有主要 raster panel，但缺少若干图内矢量文字、
标记以及底部 e 条形图。最终 region asset 完整包含 a–e，尤其底部 e panel
和图内文字/矢量标记真实进入 PDFium 裁剪。图像不含 Nature 页眉、caption、
页脚或正文；页面面积比 0.5012。Markdown 中 asset 紧邻 Fig. 2 caption。

## 视觉门禁

- 必要目标 RW2-005 p3：通过；
- RW2-005 p7：按冻结规则安全拒绝；
- RW2-007 p5：适用且通过；
- 空白、文件存在、exit 0、对象数变化均未单独作为视觉通过依据。
