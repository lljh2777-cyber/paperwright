# Completeness Gate v0.1

## 1. 目的

Completeness Gate 防止源 PDF 页面或主要视觉证据在投影到 Markdown 时静默消失。
它是确定性验证层，不调用 OCR、LLM 或视觉模型，也不尝试理解或转写科研内容。

每个源页面在写出结束时必须处于以下状态之一：

| 状态 | 含义 | 发布行为 |
|---|---|---|
| `accepted` | 已投影正文/视觉资产，或源页确认空白 | 正常发布 |
| `suspicious` | 有投影，但存在孤立 caption 或矢量视觉缺失信号 | 降级发布并报告 |
| `human_required` | 非空源页无法生成任何可靠投影 | 显式待人工，不得静默通过 |
| `invalid` | 有可用原生文字，但写出结果没有处理该页文字 | hybrid 阻断编译 |

## 2. 确定性整页兜底

当页面同时满足以下条件时，writer 会直接从源 PDF 渲染整页 PNG：

- 没有可用原生文字；
- 当前没有正文投影；
- 当前没有视觉资产；
- PhysicalDocument 仍有 image/vector 非空证据。

兜底图保留整页内容，`ocr_used` 固定为 `false`。direct 输出使用
`images/page-NNN-fallback.png`，hybrid 输出使用
`images/page-NNNN-fallback.png`。渲染失败会转为 `human_required`，并记录触发原因。

## 3. 结构化契约

所有 writer 都写出：

```text
_paperwright/completeness-report.json
```

契约版本为 `paperwright-completeness-v0.1`，JSON Schema 位于
`src/paperwright/schemas/completeness.schema.json`。报告包含：

- 文档级 `status` 与四类页面计数；
- 每页的原生 text/image/vector 证据计数；
- 每页的正文块、视觉资产和整页兜底计数；
- 页面状态、原因以及可定位的 findings。

manifest 的 `completeness` 字段保存报告路径、SHA-256、状态和摘要；报告本身也必须在
`outputs` 清单中以 `completeness_report` 角色登记。hybrid validation report 同时暴露
`page_completeness` quality check 和 `page_completeness_valid` 硬检查。

## 4. v0.1 风险信号

以下情况不会被错误标记为成功，而会成为 `suspicious`：

- caption region/candidate 没有绑定视觉对象；
- 同页有明确 Figure/Table caption 和密集 vector 证据，但没有视觉资产。

这些信号只负责发现风险，不自动扩大截图区域。后续 issue-level routing 应依据 finding
只升级相关区域，而不是重跑整篇论文。

## 5. 已验证回归

项目内合成夹具覆盖三类页面：原生文字页、纯矢量非空页、真正空白页，并同时验证
direct 与 hybrid writer、reader graph 和 manifest 哈希链。

真实论文定点回归使用基线 A06 的 32 页论文。此前静默为空的第 31–32 页现在分别生成
完整整页资产；目视检查确认 reporting-summary 表单内容完整可见。回归产物遵循
`docs/STORAGE_POLICY.md`，不进入仓库。

## 6. 已知边界

v0.1 保证的是“页面不会无声地完全消失”，不是“每一个 Figure 都已正确裁剪”。有文字层
的复杂矢量 Figure 仍可能需要 ROI 级证据、issue-level routing 和视觉复核。报告中的
`suspicious` finding 正是下一阶段的输入，不能当作无条件通过。
