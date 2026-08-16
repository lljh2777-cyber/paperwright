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
    五阶段状态与最终文档包哈希复核。
11. 跨页 Figure/Table–caption：跨页 issue scope、成对页面关系任务、显式接受/拒绝和
    ArticleModel/Reader `caption-of` 投影。
12. Caption–visual 关系标注集：哈希绑定的外部数据契约、silver/gold 分级、16 个真实
    相邻页 seed 样本和确定性校验工具。
13. Hybrid run v0.2：把 resolver 拆成 evidence/layout/projection/text/verification，完成
    阶段逐产物哈希绑定；失败恢复跳过已完成阶段，投影包和文本派生包只在完整校验后复用。
14. 独立出版社 holdout：冻结 6 篇、136 页、6 个版式体系，在 `cf71d02` 首次运行后
    核实 17 个跨页假阳性；以行首锚、页顶方向证据和本页视觉优先规则修正后降为 0，
    旧 16 例 seed 回归仍为 TP=10、FP=0、FN=0、TN=6。
15. 跨页 caption 挑战集 v0.2：冻结 8 篇/171 页自然版式批次与 4 篇/71 页显式标记
    挑战集；后者含 7 个出版社明确正例和 1 个裸面板标签负例。基线 TP=7、FP=1、FN=0，
    收紧裸 `Figure 1A` 锚点后 TP=7、FP=0、FN=0，旧 seed 回归保持不变。

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
- Hybrid run v0.1 在真实论文 *Attention Is All You Need*（15 页）完成过历史离线 evidence
  检查点：发现 15 个 L2 局部 issue（覆盖 14 页），15 页关系任务共保留 160 个候选；
  `run.json` 正确停在 `confirm_content_roi`，且通过公开契约校验。该检查未调用模型。
- Caption relation seed v0.1：9 篇、16 个真实相邻页样本，含 10 正例和 6 困难负例。
  新鲜 standard evidence 校准得到 TP=10、FP=0、FN=0、TN=6；这是调参集结果，不是
  holdout 泛化结论。合成正例仍覆盖 task/review/writer/Reader 全链。
- Caption challenge v0.2：自然抽取的 8 篇/171 页未出现正例；marker-selected 的 4 篇/
  71 页含 7 个显式正例，调整后全部召回且 1 个裸面板标签误报被消除。该集合已参与修正，
  不能估计自然分布指标，详见 [CAPTION_CHALLENGE_V0.2](CAPTION_CHALLENGE_V0.2.md)。

## 仍是兼容层的部分

- `routing.json` 的页级单标签语义；主语义已经是 `issue-routing.json`。
- 旧 visual-direct 自由画框协议；仅在没有候选时回退。
- `tools/run_routing_plan.py` 仍是默认过渡 resolver；v0.2 已把它按 layout/projection/text
  分阶段调用，但各阶段的具体执行实现尚未全部迁入核心包。
- direct CLI/manifest 仍保留兼容，但不应继续增加新能力。

## 下一阶段

由人工逐例复核 marker-selected 挑战集的 8 个稳定样本；签署完成后才能从 silver 晋升为
gold。随后再冻结未参与任何规则修正的随机论文 holdout，分别报告候选召回、候选精确率
和自然 prevalence。不能用当前 seed、两个已用于修正的 holdout 或挑战集报告总体泛化
性能。
