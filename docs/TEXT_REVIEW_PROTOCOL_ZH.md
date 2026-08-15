# paperwright 文本复核协议 v0.2

## 目标与边界

混合布局阶段由人工或视觉模型只决定页面几何和语义区域；paperwright 从原生 PDF
文字层恢复正文并生成规范 `article-model.json`。文本复核阶段再把该模型投影成
不含页面图像、source span、资产与关系的 `text-task.json`，供纯文本模型做保守整理。

paperwright 核心不会自行调用模型、外部 API 或云服务。主 Agent 负责选择具体模型、
确认隐私范围、传递任务、接收 JSON，并调用本地验证器。

## 命令

```bash
paperwright text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON
paperwright validate-text-task TEXT_TASK_JSON --article-model ARTICLE_MODEL_JSON
paperwright validate-text-review TEXT_REVIEW_JSON --task TEXT_TASK_JSON
paperwright text-apply ARTICLE_MODEL_JSON TEXT_TASK_JSON TEXT_REVIEW_JSON REVIEWED_MODEL_JSON
paperwright text-package SOURCE_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_PACKAGE
paperwright text-package SOURCE_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_PACKAGE \
  --synthesis-run SYNTHESIZE_RUN_JSON   # 可选：L3 溯源，写 manifest v0.11
paperwright validate-text-package OUTPUT_PACKAGE
```

所有输出文件都拒绝覆盖。`text-apply` 只生成新的 Article Model；`text-package`
则保留源包不变，原子写出完整的 manifest v0.10 派生包，重新投影 `article.md`、
`reader.json` 并加入 task、review 与验证报告。首版只接受完整的 manifest v0.9
源包，避免脱离已验证的视觉布局来源。

## 允许的操作

### `replace-markdown`（每个 block 最多一次）

- `format-only`：规范化可见文本必须与原文完全相同，可用于 Markdown 强调与空白整理；
- `dehyphenation`：只能删除词内断行形成的连字符及其后空白，例如
  `multi- modal` → `multimodal`。

### `join-blocks`（模型无关的跨块段落拼接）

确定性合并覆盖常见双栏版式，但无法保证每种期刊布局。复核模型读到断句信号
（独立块以小写字母开头、或前块不以句末标点结尾而后块以小写开头）时，可声明
一对阅读顺序相邻的块是同一段落，由校验器执行纯拼接：

```json
{
  "op": "join-blocks",
  "target_block_ids": ["blk_a", "blk_b"],
  "reason": "Same paragraph split at a column boundary."
}
```

校验器强制（全部满足才通过）：

1. 两块都是 `body`、可编辑、不参与任何 Figure/Caption 关系；
2. 同一页，且 `order` 相邻；
3. 前块不以 `.!?:;` 结尾、后块首字符是小写字母 —— 续行的可证明信号；
4. 结果 = 前块文本 + 空格（前块以连字符结尾时无空格）+ 后块文本，零改写。

前块保持其稳定 ID、source span 与顺序，仅文本增长；后块被移除，其元素在
`physical_document.json` 物理层仍可溯源。同一块不能在多个操作中出现（链式
拼接 A→B→C 需先拼 A+B 应用，再拼其结果与 C）。

### 边界

视觉槽位不可编辑。`replace-markdown` 不改稳定 ID、block kind、order、source
span、asset ID、资产与关系；`join-blocks` 是唯一允许改变 block 结构与顺序的
操作，且只做同页相邻 body 的纯拼接。v0.2 不允许拼写/标点/事实改写，不允许
拆分或删除正文，不允许改变 Markdown 标题层级，也不允许依据模型知识补写正文、
图注、公式或引用。规则与协议都覆盖不到的复杂版式，可由 L3 程序合成桥写受限 DSL
（`tools/run_text_synthesize.py`）：脚本与输入/输出哈希一并写入
`synthesize-run.json`，`text-package --synthesis-run` 将其落入
`_paperwright/06-text-review/` 并在 manifest v0.11 哈希链中做确定性重放。
保守脚本路径仍见 `skills/paperwright-convert/references/join-blocks-protocol.md`。

## 哈希链

Text Task 记录源 PDF SHA-256、Article Model 契约和规范 JSON SHA-256；每个 block
另记录原 Markdown 与规范化可见文本哈希。Text Review 必须回传 task、source、model
哈希，并为每次替换回传目标 block 的原 Markdown 哈希。任何过期或串线任务都会
明确失败，不会尝试模糊匹配。

派生包的 manifest v0.10 还记录父 manifest、源 Article Model、task、review 和
JSON 验证报告的 SHA-256。输出清单逐文件绑定全部交付文件，因此修改正文、Reader、
图片、复核记录或报告中的任意一个文件都会使完整包校验失败。

## 多 Agent 分工

- 视觉子 Agent：只接收页面图、ROI、布局任务与几何说明，只返回 final layout JSON；
- 文本子 Agent：只接收 text task JSON，只返回 text review JSON；
- 主 Agent：保管原 PDF 与哈希链，运行所有验证命令，决定是否接受并交付新模型。

仓库内 [`paperwright-agent-workflow`](../skills/paperwright-agent-workflow/SKILL.md)
skill 固化了这一协调流程。
