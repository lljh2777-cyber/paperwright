# 未见论文 L2 完整生产链回归 v0.1

## 目的与冻结边界

本轮验证提交 `f3ac41ef1b244773fe7cfc81ed833fc47036077e` 修复后，真实 Hybrid
产品路径能否在新科研论文上完成页面级 L2，而不是再次绕过布局桥构造 task adapter。

执行前冻结以下条件：

- 沿随机 holdout v0.5 的 PMC OA 候选顺序从位置 55 开始；
- 只按文章类型、同行评议、OA PDF、原生文字层和未见性筛选，直到纳入 3 篇；
- 不因版式、L2 页数、难度或最终失败换样；
- 执行代理固定为 `gpt-5.6-luna` high，用于模拟较低能力 Agent 的实际操作；
- 视觉模型单独固定为 `qwen3.7-plus`、temperature 0；
- 禁止手写或修补 relation task/review、candidate bbox 和 final layout；
- 不使用 token budget 或费用门禁。

仓库外完整协议、资格记录、PDF、运行目录和输出包位于：

```text
paperwright-l2-fullchain-unseen-v0.1/
```

## 语料

按位置 55–64 检查 10 个候选，排除 7 篇综述、指南、预印本、letter 或病例报告，纳入：

| ID | 位置 | PMCID | 页数 | 类型 |
|---|---:|---|---:|---|
| U01 | 57 | `PMC12179955` | 11 | 肝癌术后 VTE 列线图回顾性研究 |
| U02 | 59 | `PMC12946325` | 8 | Norwood 术后声带功能质量改进研究 |
| U03 | 64 | `PMC12195943` | 17 | 山羊酪蛋白肽组学计算研究 |

冻结的 U01 PDF URL 和 U02 tarball URL 在执行时返回 404；Luna 代理没有换文档，而是按
同一 PMCID 改用官方 PMC OA S3 PDF，并在 corpus 中记录 fallback。三篇均逐页检查规则
ROI 提案并原样确认；未修改 bbox。

## 结果

| 文档 | 正式布局页 L0/L2 | L2 issue/覆盖页 | 结果 | 终止点 |
|---|---:|---:|---|---|
| U01 | 6 / 5 | 5 / 5 | fail | 第 1 页连续 3 次：文本 role 的 `content_class` 非法 |
| U02 | 5 / 3 | 3 / 3 | pass | 五阶段全部完成 |
| U03 | 8 / 9 | 10 / 9 | fail | 第 1 页：header/margin 未使用 `exclude` |

完整链通过率为 **1/3**。两个失败都发生在正式 `run_visual_review.py` 页面关系契约，
没有进入 projection；本批没有观察到确定性 projection 错误或 Luna 命令执行错误。U02
的 3 个 L2 页分别发生 2、1、3 次 provider call，最终均生成 production relation review
与 final layout；无 order normalization warning 或 ROI clip warning。

根代理独立验证：

- 3 个 `run.json` 契约均 valid；U01/U03 的业务状态仍严格保持 `failed`；
- U02 的五个 Hybrid 阶段均 completed；
- U02 的 Reader、ArticleModel、manifest v0.10 文本派生包均 valid；
- U02 的 8 个 final layout 与 3 个 L2 relation review 均 valid。

没有使用 frozen-issue adapter，没有修补模型 JSON，也没有因失败替换论文。供应商 usage
只保留作 provenance，不参与结论。

## 解释与下一目标

这 3 篇只能暴露生产链故障族，不能估计科研 PDF 的总体布局准确率。结果说明上一步已经
修复候选遗漏、顺序和 ROI 结构问题，但较低稳定性的模型仍会在冗余字段之间产生不一致：
例如 role 已是 `header`，`content_class` 却不是 `exclude`；或文本 role 搭配非法 class。

后续实现已把这类**可由明确 role 推导**的冗余字段一致性纳入确定性规范化，同时继续
拒绝候选分组、caption parent、Figure/Table 角色等真正的语义冲突。使用本批设计修复后，
本批立即转为开发集；完整链独立复验必须使用候选位置 65 之后的新论文。

## 修复后的开发集回放

Luna-high 在全新的 run/output 根中只重跑原先失败的 U01、U03，未覆盖本页记录的原始
unseen 证据。两篇均沿真实 production Hybrid 路径完成五阶段；共验证 28/28 个 final
layout、14/14 个 page relation review，以及两篇各自的 run、Reader、ArticleModel、
text package 和跨页 task/review。U01 的 5 个 L2 页发生 7 次 role/class 规范化，U03 的
9 个 L2 页发生 2 次；没有 task adapter 或人工修补模型产物。

完整回放记录位于仓库外的 `paperwright-l2-fullchain-development-replay-v0.1/`。这一结果
只证明本次冗余字段修复消除了已知生产链停止点，不是新的 independent unseen 结果，也
不证明模型给出的 role 本身或最终 Markdown 阅读质量正确。

## 外部证据哈希

- `EVALUATION_PROTOCOL.md`：`6e76a790d20cd251d4c08c9cd71fbec390daa82c52b180ea7b63ce2e3f2b3bfb`
- `ELIGIBILITY.json`：`3c194a7a7d526bb7dced0c656654dbd4d1aae335596a4cf54fa87a5160ab93bc`
- `CORPUS.json`：`8ba915f7e2d8afef201bd1eb3933667a9e4948da7e78cf000af2774b44f2b084`
- `evaluation/results.json`：`266517f6c67f2310ccf09fa23d08269569c1822be644ea5db64b037b942b50fd`
- `RESULTS.md`：`0f0b7bd5f13a9a395791623ead1e3834c4978291cf52b5e9c6a659ee5668402e`
