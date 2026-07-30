# Phase 4 region-render spike 变更说明

本次只实现被授权的 clipped page-region render 技术验证，没有铺开完整
Phase 4。

## 产品变更

- 新增 `paper2md.region_render`：同页 caption、image、vector、text 与原生
  外框的保守规划器；
- PDFium adapter 新增真实 `page.render(crop=...)` 路径，记录 scale/DPI、
  rotation、像素、源 PDF 与运行时身份；
- 新增越界、caption guard、近整页、像素上限及空白/恒定图硬拒绝；
- writer 保留原始 embedded/grouped 资产，仅将通过防护的裁剪作为主 Figure
  资产；
- manifest 升至 v0.4，增加 `native_asset` 与 `region_render`；
- 功能默认关闭，必须显式给定页面索引白名单，避免 spike 扩散到未冻结页。

## pre-fix → final

第一次真实矩阵的像素资产已满足视觉门槛，但 manifest 把区域 vector count
扩展到完整集合时，仍沿用了 Phase 3 bitmap-group 子集哈希。该问题不影响
图像内容，但会使追溯口径不一致。最终代码改为对完整 region vector ID
集合计算哈希，并在新目录执行完全相同的冻结矩阵。v1 runtime 保留未覆盖；
两版 region PNG 字节哈希完全相同。

## 未扩大范围

没有新增 OCR、表格语义、公式 LaTeX、页眉页脚规则、PDFBox 评分、论文
样本、发布打包或二进制分发。
