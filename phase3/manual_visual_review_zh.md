# Phase 3 人工视觉检查

检查方法：使用 PDFium 将原 PDF 指定页按 1.35 倍渲染，与 Phase 3
`manifest.json` 指向的 Figure 资产并排查看；逐项确认可见内容、是否整页
截图、Figure 范围、caption 身份和 Markdown 邻接。接触图只留在云端临时
证据目录，不进入源码包。机器哈希与逐项结论见 `visual_review.json`。

## 八篇主样本

| 论文 | 页 | 观察类型 | 结论 |
|---|---:|---|---|
| RW2-001 | 4 | 视觉 | A–D 面板完整；原生嵌入图；Figure 1 caption 正确邻接。 |
| RW2-002 | 4 | 视觉+文本 | FBSDE 主图完整；caption 的碎片首行被几何合并，未把正文引用当 caption。 |
| RW2-003 | 2 | 视觉 | 神经网络 Figure 1 完整，未吞并右栏正文。 |
| RW2-004 | 3 | 视觉 | PCA 点、标签、图例完整；caption 正确。 |
| RW2-005 | 3 | 视觉 | 14 个 baseline 碎片改善为 12 对象组合图加 2 个独立 plot；仍不是完整单一 Figure，标为 partial/degraded。 |
| RW2-006 | 3 | 视觉 | ATAD1 A–G 面板完整；caption 正确。 |
| RW2-007 | 3 | 视觉 | 空间转录组多面板图完整；非整页 render。 |
| RW2-008 | 3 | 视觉 | UMAP 九面板完整；页眉 logo 未被提升为 Figure。 |

另外检查 RW2-005 第 7 页失败样例：系统输出三个可见但不完整的组，因
caption 距离超过冻结阈值而拒绝配对。该结果没有伪装成成功。

## 自动检查与人工观察的边界

- 自动检查：8/8 标题、138/138 页标记、97 个原生图片对象、41 个
  Figure group、资产 hash/尺寸/非恒定像素、元素/page/bbox/caption
  引用、27 个 matched association 的同页与 Markdown 前置邻接、4/4
  双轮确定性。
- 人工观察：8 篇各一个主 Figure 加 1 个失败样例，共 9 个接触图。
- 推断：`vector_evidence` 只表示矢量 bbox 与 Figure 区域重叠，不表示
  矢量已经渲染或恢复。

人工主样本结果为 7 pass、1 partial；未观察到空白图、整页截图冒充
Figure 或跨页错误配对。未逐一视觉确认的 19 个 matched association
仍是明确限制。
