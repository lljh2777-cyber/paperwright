# Visual Candidate Relations v0.1

## 1. 为什么替换自由画框

旧视觉桥要求模型直接输出归一化 bbox、区域角色和整页阅读顺序。不同视觉模型的画框
精度与顺序稳定性差异会直接进入最终产物，也会迫使模型重复判断确定性代码已经获得的
几何信息。

`paperwright-visual-relation-review-v0.1` 将模型职责缩小为：

- 哪些候选属于同一逻辑区域；
- 区域是正文、Figure、Table、caption 或页面家具；
- 非排除区域的阅读顺序；
- caption 属于哪个 Figure/Table。

模型不能输出 bbox、正文、OCR、Markdown、source element ID 或新候选。

## 2. 输入

`layout-prepare` 在每个有候选的页面目录增加：

- `visual-relation-task.json`：文字无关的 LayoutTask 候选、数值/模式特征和 task hash；
- `candidate-overlay.png`：在原页面上标出 candidate ID；
- `issue-routing.json` 中命中该页的局部问题。

若 Figure caption 已由问题级路由定位，但因栅格重叠抑制而没有独立候选，系统会把
issue scope 已有的 bbox 和 element ID 变成只读 `Ixxx` anchor candidate。anchor 不是模型
画出的框，也不会引入新识别规则；它只是把已经存在的确定性证据显式送入关系任务。

## 3. 审查契约

模型输出 `visual-relation-review.json`：

```json
{
  "contract_version": "paperwright-visual-relation-review-v0.1",
  "source_sha256": "...",
  "page": {},
  "task_sha256": "...",
  "reviewer": "model-id",
  "prompt_version": "paperwright-visual-relations-prompt-v0.2",
  "groups": [
    {
      "group_id": "figure",
      "candidate_ids": ["C003", "C004"],
      "content_class": "visual",
      "role": "figure",
      "order": 1,
      "parent_group_id": null,
      "confidence": 0.9
    }
  ],
  "discarded_candidate_ids": [],
  "warnings": []
}
```

校验器强制：

- 每个候选恰好进入一个 group 或 discard；
- group/candidate/parent 引用守恒且无环；
- 非排除顺序从 1 连续；
- 高置信 caption 不得丢弃，必须为 caption group 并绑定 visual parent；
- compound raster 必须保留为 Figure/Table；
- role、content class、confidence 和文档/task 身份合法。

prompt v0.2 后的确定性规范化不代替语义校验。程序把非排除 group 的 `order` 规范为
连续序列，并把 exclude 的 `order` 置空；当模型已经给出明确 role 时，还会修正与 role
冲突的冗余 `content_class`：`header/footer/margin → exclude`，明确文本 role → `text`，
明确视觉 role → `visual`。文本/视觉 role 的 `unknown` 保留，不从 `unknown/other` role
推导 class。每次修正均写入稳定 warning。

候选遗漏、重复、group/role 判断、caption parent、discard 和 confidence 不会被修正，
仍由校验器拒绝。这里把 role 当作模型已作出的语义决定，只消除其与冗余 class/order 的
自相矛盾；它不能证明 role 本身正确。桥接重试会把上一次确定性校验错误加入下一次提示，
要求模型重新返回完整响应，避免 temperature 0 下原样重复无效 JSON。
新写入使用 prompt v0.2；v0.1 review 继续可读，以便回放既有 evidence。

Schema 位于 `src/paperwright/schemas/visual_relation_review.schema.json`。

## 4. 确定性编译

`compile_visual_relation_review` 对每个 group 取候选 bbox 的精确并集，映射父子关系，生成
`add` / `attach-caption` 动作，然后调用现有 `validate_layout_review`。若非排除并集只是在
已确认 Content ROI 边界上越界，编译器确定性求交，并在 layout warning 与 add action
reason 中记录裁剪；完全位于 ROI 外仍拒绝。模型没有修改几何的接口。

视觉桥 `--protocol auto` 优先使用该协议。候选不存在时才回退旧 regions 画框协议；
`--protocol relations` 可要求必须使用关系协议。standard/full evidence 会保存 relation
review 和 candidate overlay，full 还保存 relation task。

## 5. A06 回归

32 页 A06 论文的 7 个 L2 页均生成了关系任务：

- Figure 页 3、4、5、8、9、10 均同时具有 caption candidate 与视觉 candidate；
- 其中 5 页使用 issue anchor，第 8 页复用原生 caption candidate；
- 复杂几何页 28 有 24 个候选；
- 第 8 页真实任务用 Figure + 跨栏 caption + footer 关系完成确定性编译，并通过
  `validate-final-layout`。

本轮没有调用外部视觉模型，因此这里只证明任务完整性、契约守恒和编译链路；不同模型
在关系判断上的准确率仍需后续标注集评估。
