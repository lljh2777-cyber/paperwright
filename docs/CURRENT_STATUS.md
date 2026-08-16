# PaperWright 当前实现状态

更新时间：2026-08-16。

## 产品边界

- 只面向 born-digital 科研论文；扫描 PDF/OCR 不是当前核心范围。
- 只发展 hybrid：L0 确定性内核是流水线基础，不再发展独立 direct 产品语义。
- AI 只处理有证据的未决判断；所有模型输出必须经过确定性契约、守恒和回放校验。
- 路由不使用模型价格、token 预算或供应商身份。

## 已完成

1. 科研论文质量基线：13 篇、305 页，八维标注与失败分类。
2. L0 提取与投影：正文、Figure、同页 Table、独立 Equation、页眉页脚和引用处理。
3. L1 文本判断：只判断验证器允许的相邻 block 是否应 join，不重写正文。
4. L2 视觉判断：DashScope 直连桥、问题级上下文和候选关系协议。
5. L3 程序合成：受限 DSL、守恒校验、重放溯源和 L1 失败降级。
6. Completeness Gate：逐页状态、无文字非空页整页视觉保留、孤立 caption/漏投影回流。
7. Issue-level Routing：所有页面以 L0 为基础，局部问题才升级；布局后精确发现 L1。
8. Visual Candidate Relations：模型只分组/分类/排序/绑定，bbox 由候选并集确定性生成。
9. ArticleModel、Reader、manifest、evidence 和 SHA-256 绑定。
10. HybridPipeline/run contract：唯一 `paperwright hybrid` 入口、ROI 安全暂停/恢复、
    三阶段状态与最终文档包哈希复核。
11. 跨页 Figure/Table–caption：跨页 issue scope、成对页面关系任务、显式接受/拒绝和
    ArticleModel/Reader `caption-of` 投影。

## 当前主链

```text
PDF
 → PhysicalDocument + raster evidence
 → issue-routing.json
 → L0 layout
 → L2 candidate relation review（仅局部视觉 issue）
 → deterministic FinalLayout compiler
 → paired-page caption relation review（仅跨页候选）
 → ArticleModel + Markdown + assets
 → Completeness Gate
 → exact L1 block-pair discovery
 → L1，失败时 L3
 → resolve-issues.json（若仍有局部问题）
```

## 真实论文校准结果

- 基线：13 篇、305 页。
- A06 Completeness：原空白输出的第 31–32 页改为整页视觉保留。
- A06 issue routing：32 页均以 L0 为基础，仅 7 页需要视觉关系判断。
- A06 visual relations：6 个 Figure 页均有 caption + visual 候选，第 28 页保留 24 个复杂
  候选；真实第 8 页任务已通过关系编译与最终布局校验。
- Hybrid run v0.1 在真实论文 *Attention Is All You Need*（15 页）完成离线 evidence
  检查点：发现 15 个 L2 局部 issue（覆盖 14 页），15 页关系任务共保留 160 个候选；
  `run.json` 正确停在 `confirm_content_roi`，且通过公开契约校验。该检查未调用模型。
- 跨页 caption v0.1 对 16 份 review bundle、305 页离线扫描产生 0 个跨页 issue；未增加
  已有语料误报，但语料也不含已确认正例。合成正例已通过 task/review/writer/Reader 全链。

## 仍是兼容层的部分

- `routing.json` 的页级单标签语义；主语义已经是 `issue-routing.json`。
- 旧 visual-direct 自由画框协议；仅在没有候选时回退。
- `tools/run_routing_plan.py` 仍是 `HybridPipeline` 调用的过渡 resolver，内部阶段尚未全部
  上移为细粒度原子检查点。
- direct CLI/manifest 仍保留兼容，但不应继续增加新能力。

## 下一阶段

建设关系判断标注集和真实跨页正例集；同时把过渡 resolver 内部的布局、投影与文本阶段
上移为细粒度可恢复检查点。
