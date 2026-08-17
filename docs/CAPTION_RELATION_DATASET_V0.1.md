# Caption–visual 关系标注集 v0.1

## 目标与边界

这个标注集只服务于 born-digital 科研论文中的 caption–visual 关系判断，当前首个任务是
“前一页 Figure/Table 是否由后一页 caption 描述”。它不是论文全文 corpus，也不标注
论文科学内容。

真实 PDF、页面 PNG 和逐例标注放在仓库外。仓库只发布契约、Schema、校验工具和去正文
聚合结论，遵守 `STORAGE_POLICY.md`。

## 契约

版本：`paperwright-caption-relation-dataset-v0.1`。

每个 document 保存稳定 ID、源 PDF SHA-256 和页数；每个 example 保存：

- 相邻 visual/caption 页的零基索引；
- `figure` / `table` 与 `positive` / `negative` / `uncertain`；
- caption 锚元素、最多 160 字符的短前缀、完整锚文本哈希和归一化 y；
- 两张证据页的 SHA-256 与结构信号；
- 审阅者、置信度、理由代码及原始质量标注来源。

Schema 位于 `src/paperwright/schemas/caption_relation_dataset.schema.json`。Python 校验器
还检查文档引用、相邻页关系、页码范围、ID 唯一性和 summary 守恒。

## Silver 与 Gold

- `silver`：页面图、原生文本、显式续页标记和既有模型审阅互相印证，但尚未由人工
  独立确认；
- `gold`：每个样本都必须是 `human_verified`。校验器拒绝把模型 seed 直接改名为 gold。

v0.1 seed 是 silver。它适合发现规则漏召回、建立困难负例和回归测试，不适合宣称最终
模型准确率。

## Seed v0.1

仓库外位置：

```text
paperwright-evaluation-v0.1/caption-relation-dataset-v0.1/seed-v0.1.json
```

聚合结果：

| 项目 | 数量 |
|---|---:|
| 论文 | 9 |
| 样本 | 16 |
| 正例 | 10 |
| 困难负例 | 6 |
| 含正例论文 | 5 |
| Figure | 13 |
| Table | 3 |

正例覆盖：后一页顶部 caption、后一页底部 caption、`FIGURE 3` 这类独立标签、前页
“see next page for caption/legend”和后页 `◀` 方向标记。困难负例覆盖 caption 位于页顶但
视觉对象实际也在本页的 Figure/Table。

```bash
PYTHONPATH=src python tools/validate_relation_dataset.py \
  /path/to/seed-v0.1.json
```

工具拒绝非法字段、重复 ID、非相邻页、哈希格式错误、过期 summary，以及没有人工确认
却声称为 gold 的数据。

## 本轮校准

使用当前代码对 9 篇论文重新生成 standard evidence，再从相同 PhysicalDocument 和
LayoutTask 重放 issue routing：TP=10、FP=0、FN=0、TN=6。

seed 样本直接参与了规则修正，所以 precision/recall=1.0 只是内部 calibration sanity
check，不是 holdout 结果。首个 6 出版体系 holdout 用于发现和修正 17 个假阳性；后续
8 篇/171 页自然版式批次仍没有真实正例。为单独测试召回，又建立了 4 篇/8 例的
marker-selected `silver` 挑战集，含 7 个出版社显式正例和 1 个裸面板标签负例；修正前
TP=7、FP=1、FN=0，修正后 TP=7、FP=0、FN=0。详见
[跨页 Caption 挑战集 v0.2](CAPTION_CHALLENGE_V0.2.md)。Liao Li 已于 2026-08-17 完成
8/8 人工复核并生成 `gold-v0.2.json`。该集合已经参与修正，所以 gold 只表示标签经过
人工确认，仍不能作为总体泛化指标。
