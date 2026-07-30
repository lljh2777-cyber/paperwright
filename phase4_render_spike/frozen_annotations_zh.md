# Phase 4 region-render spike 冻结标注

本标注在任何候选区域裁剪生成前冻结。坐标均为
`top-left/pdf-point/y-down`，只使用同页 PhysicalDocument 对象、明确
caption marker、原页审阅图和 PDF 内原生外框证据。

- RW2-005 第 3 页：Figure 1 的原生外框
  `p0002-vector-00015` 精确包围位图面板与矢量曲线，caption 位于外框
  下方 7.731 pt。该 bbox 是必要成功目标，不能在看过裁剪后调参。
- RW2-005 第 7 页：虽然页面内存在清晰外框，但同页明确写有
  “Figure 3 continued on next page”。本 spike 禁止跨页聚合，因此冻结为
  安全拒绝，不能用单页裁剪冒充完整 Figure。
- RW2-007 第 5 页：单一原生位图在 y=444.655 结束，而 Figure 2 的
  9,553 个矢量对象和图内文字继续到 y=589.115。目标 bbox 由同页
  image/vector/text evidence 并集加 4 pt padding 得到，并保留到 caption
  的 5.364 pt 间隔。

若后续发现标注错误，只能新增版本并保留本文件、pre-fix 证据和修订理由。
