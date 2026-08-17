# PaperWright 当前实现状态

更新时间：2026-08-17。

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
16. 人工 gold：Liao Li 于 2026-08-17 复核挑战集全部 8 张成对页面图，人工标签与 silver
    标签 8/8 一致、无 uncertain；保留 silver 审计输入并生成全量 `human_verified` 的
    `gold-v0.2.json`。
17. 随机 holdout v0.3：从固定的 669 篇 PMC OA 抽样框以可重放随机顺序冻结 8 篇、
    108 页、100 个相邻页对，未按出版社或正例标记换样。`23c7aad` 路由跨页候选为 0，
    全页 silver 审计真实正例也为 0；pair-level prevalence 观察值 0%，双侧 95% 上界
    3.62%，precision/recall 因零正例不可定义。本轮未修改规则。
18. 随机 holdout 人工 gold：Liao Li 于 2026-08-17 复核 8 张全页 contact sheet，结果
    全部为 `none`，与 silver 8/8 文档、100/100 相邻页标签一致，无 uncertain；形成
    `pair-audit-gold-v0.3.json`，仍未修改规则或回填结果。
19. 随机 holdout v0.4 事前协议：固定基线 `6f322ec`，复用 v0.3 的 669 篇抽样框和候选
    顺序，从位置 13 开始，按标签无关的停止规则再纳入 12 篇合格论文；资格、评估流程、
    gold 转换条件和统计边界均在查看新页面前冻结。
20. 随机 holdout v0.4 silver：按协议检查候选位置 13–37，冻结 12 篇/169 页/157 个相邻
    页对。固定基线路由 0 个跨页候选，全页审计发现 9 个 silver 正例（4 篇论文），均为
    多面板 Figure 跨两页、caption 页仍含后续面板；gold 签署前未修改规则。
21. 随机 holdout v0.4 人工 gold：Liao Li 于 2026-08-17 复核全部 12 张 contact sheet，
    确认 9 个 silver 页对并新增 V01 14–15，共 10 个 gold 正例、无 uncertain。文档二元
    判断 12/12 一致、精确页对集合 11/12 一致、相邻页标签 156/157 一致；冻结基线
    TP=0、FP=0、FN=10、TN=147。
22. 跨页面板连续性修正：v0.4 在 gold 后明确转为开发/校准集。路由仅在“前页大型
    raster Figure 到页底 + 后页页顶后续片段紧邻 caption + 前页未被本页 caption 终止”
    时覆盖本地视觉抑制，并拒绝低残余覆盖的整页装饰。v0.4 校准回放为 TP=10、FP=0、
    FN=0、TN=147；v0.3 的 100 个 gold 负页对保持 0 候选，marker challenge 保持
    TP=7、FP=0、FN=0、TN=1。paired-page prompt 升级到 v0.2。
23. 随机 holdout v0.5 事前协议：固定基线 `e94cf33`，沿原冻结候选顺序从位置 38 开始，
    按标签无关停止规则纳入 12 篇合格论文；在查看页面前固定资格、gold 定义、确定性候选
    主指标、最终 L2 分表和“正例少于 5 个不宣称泛化验收”的解释边界。
24. 随机 holdout v0.5 资格冻结：按协议检查候选位置 38–54，纳入 12 篇同行评议原始
    研究、排除 5 篇非目标文章；冻结为 158 页、146 个相邻页对、9 个期刊。资格阶段没有
    运行 PaperWright、生成页面预览或查看跨页标签。
25. 随机 holdout v0.5 silver：固定基线产生 6 个跨页候选，全页审计得到 4 个真实正例，
    TP=4、FP=2、FN=0、TN=140，候选 recall=100%、precision=66.67%、负页对误报率
    1.41%。正例少于事前指定的 5 个，只能作探索性结果；等待 silver-informed 人工签署，
    期间不修改规则。

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
  8 个样本已完成人工复核并形成 gold，但仍不能估计自然分布指标，详见
  [CAPTION_CHALLENGE_V0.2](CAPTION_CHALLENGE_V0.2.md)。
- Random holdout v0.3：随机冻结的 8 篇/108 页形成 100 个相邻页对，当前 silver 审计没有
  正例；100 个负例已人工复核并形成 gold，规则未因结果修改。它提供自然 prevalence 的
  初步上界，但不能估计候选 precision/recall，详见
  [RANDOM_HOLDOUT_V0.3](RANDOM_HOLDOUT_V0.3.md)。
- Random holdout v0.4：事前冻结后新增 12 篇/169 页/157 个相邻页对；0 个路由候选下
  人工确认 10 个 gold 漏召回，集中为“caption 页存在同一 Figure 后续面板”的系统失败族。
  规则在 gold 签署前保持冻结；转为开发/校准集后的面板连续性路由已召回 10/10，且
  没有命中其余 147 个页对。该数字不能作为独立泛化结果，详见
  [RANDOM_HOLDOUT_V0.4](RANDOM_HOLDOUT_V0.4.md)。

## 仍是兼容层的部分

- `routing.json` 的页级单标签语义；主语义已经是 `issue-routing.json`。
- 旧 visual-direct 自由画框协议；仅在没有候选时回退。
- `tools/run_routing_plan.py` 仍是默认过渡 resolver；v0.2 已把它按 layout/projection/text
  分阶段调用，但各阶段的具体执行实现尚未全部迁入核心包。
- direct CLI/manifest 仍保留兼容，但不应继续增加新能力。

## 下一阶段

由人工逐篇复核 v0.5 的 12 张全页 contact sheet 并签署 gold；reviewer 已知 silver 汇总
与候选文档族，因此必须称为 silver-informed review，而不是盲审。入口与边界见
[RANDOM_HOLDOUT_V0.5](RANDOM_HOLDOUT_V0.5.md)。
