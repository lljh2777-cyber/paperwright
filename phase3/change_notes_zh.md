# Phase 3 代码变更与缺陷优先级

## 已实现

1. `src/paper2md/figures.py`
   - 同页原生 image object 的确定性 Figure 分组；
   - `Figure` / `Fig.` marker、几何同行合并、同页距离与水平重叠配对；
   - 歧义、无 caption、跨页和小型 logo 的拒配/过滤原因；
   - 多位图按 PDF bbox 组合，保留全部原始资产和元素 provenance；
   - 矢量 bbox 只作 evidence，未渲染时明确 degraded。
2. `src/paper2md/writer.py`
   - 高置信 Figure 在 caption 之前局部插入；
   - 未配对 Figure 保持页末降级；
   - 原生图片、组合图、caption text/hash、bbox、成员与来源均写入 manifest。
3. `manifest v0.3`
   - 新增 `figures`、`figure_rejections`、caption association、
     `vector_evidence` 和 Markdown placement；
   - 保留 v0.2 核心字段，迁移说明见 `docs/MANIFEST_MIGRATION_V0.3.md`。

## 真实样本驱动的最小修正

- RW2-002 caption 被 PDF 拆成多个同行 text object：增加独立的几何同行
  合并，避免只依赖 Stage C `line_group`。
- RW2-001/RW2-002 的 `Fig 2c)` 等正文子图引用：增加右括号规则并拒绝
  当作 caption。
- RW2-005 位图组合仍缺矢量内容：撤回“完整组”表述，改为
  `degraded_bitmap_group_with_unrendered_vector_evidence`。

## 未实现与优先级

1. P1：同一 Figure 的位图与矢量区域裁剪渲染；RW2-005 第 3/7 页仍
   是主要失败样例。
2. P1：给 caption 建立更细的真实 gold，逐个校准 27 个自动匹配，而非
   只视觉抽查 8 个主样本。
3. P2：重复页眉页脚抑制。本轮没有证明可在不伤正文的情况下安全处理，
   因而未改代码。
4. P2：纯矢量 Figure、跨页 Figure continuation 与更复杂的 caption
   continuation；仍禁止跨页强配。
5. 不在范围：OCR、语义表格、公式 LaTeX、LLM/API、PDFBox 完整评分、
   发布打包和二进制许可证批准。
