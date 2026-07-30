# manifest v0.3 → v0.4

v0.4 为 Figure 记录增加两个必需字段：

- `native_asset`：始终记录并保留 Phase 3 的 `embedded` 或 `grouped`
  资产及哈希；
- `region_render`：记录 `not_requested`、`rejected` 或 `rendered`，
  并在渲染成功时给出 PDF-space bbox、scale/DPI、rotation、像素尺寸、
  方差、页面面积比、源 PDF 哈希、PDFium 版本与 bbox 规则。

`extraction_mode` 新增 `region-rendered`。只有 PDFium 实际执行 clipped
page render 且通过越界、caption guard、近整页、像素上限与空白检查后，
该值才允许出现。PhysicalDocument v0.2 不变；现有元素、图片、Figure
成员与 source object provenance 不删除。
