# 跨页 Caption 挑战集 v0.2

## 为什么拆成两个语料批次

上一轮 6 篇独立出版社 holdout 只暴露了假阳性，没有真实正例。直接再按“图很多的论文”
抽样仍不能保证出现跨页关系，因此本轮明确区分两种用途：

1. **自然版式批次**：冻结 8 篇、171 页、4 个版式系列，不查看标签先运行
   `a869281`；结果没有显式跨页标记，也未观察到真实跨页图注。它只能作为同页版式和
   困难负例回归语料，不能评估跨页召回。
2. **显式标记挑战集**：另选 4 篇、71 页、3 个版式系列，入选条件是出版社原文含
   `See next page for legend`。该标签独立于 PaperWright 路由，适合检验“已知正例能否被
   召回”，但由于按正例标记富集，不能估计自然分布中的 prevalence 或总体准确率。

PDF、页面图、逐例数据和裁决表均保存在仓库外：

```text
paperwright-caption-holdout-v0.2/
paperwright-caption-challenge-v0.2/
```

仓库只记录来源边界、去正文聚合结果与通用规则调整。

## 冻结语料

自然版式批次包含 Cell Genomics、Cell Reports Medicine、Cell Reports Methods 和
Science Advances，各 2 篇。显式标记挑战集包含 3 篇 Development 和 1 篇 Journal of
Cell Science。两批均为 born-digital 开放获取科研论文，使用 `standard` evidence，不调用
外部模型。

显式标记挑战集首先形成 `silver-v0.2.json`：4 篇、8 个相邻页样本，其中 7 个正例、1 个
困难负例。每个正例都同时具有出版社方向标记、前页视觉证据和后页 caption；负例是补充
材料中的裸面板标签 `Figure 1A`，它位于本页 blot 内，不是上一页视频帧的 caption。

2026-08-17，Liao Li 对全部 8 张成对页面图完成独立人工复核，人工标签与 silver 标签
8/8 一致，无 `uncertain`。原 silver 文件保留作为审计输入，人工结果另存为
`gold-v0.2.json`；其中每个样本均标记为 `human_verified` 并记录审核人和日期。

## 基线与调整

挑战集在 `a869281` 上先冻结再运行：

| 指标 | `a869281` | 调整后 |
|---|---:|---:|
| 全部布局 issue | 50 | 41 |
| 同页 caption–visual issue | 40 | 32 |
| 跨页 caption–visual issue | 8 | 7 |
| TP | 7 | 7 |
| FP | 1 | 0 |
| FN | 0 | 0 |
| TN | 0 | 1 |

调整只收紧 caption 锚点：无标点、带面板字母的裸标签（如 `Figure 1A`）不再被视为完整
caption；带显式 previous-page 方向标记时仍可覆盖。`FIGURE 3` 这类整图独立标签继续保留。

原 9 篇/16 例 seed set 从缓存的 PhysicalDocument 和 LayoutTask 重新回放，仍为
TP=10、FP=0、FN=0、TN=6。本轮挑战集已经参与该规则修正，从现在起也只作为回归集。

## 结论与边界

- 目前已有一组由出版社显式方向标记支持的真实正例，可验证规则召回；
- 结果说明确定性 evidence 可以先缩小范围，视觉模型只需处理候选关系，不必扫描全文；
- 挑战集是 marker-selected，不是随机 holdout，不能报告总体 precision/recall；
- 8 个样本已完成人工签署并晋升为 `gold`，可作为固定回归集；
- 人工签署提升的是标签可信度，不改变 marker-selected 的抽样偏差，因此仍不能用它报告
  自然 prevalence 或总体泛化性能。下一步应冻结真正随机、未参与规则修正的新 holdout。
