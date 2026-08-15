# join-blocks: 模型无关的跨块段落拼接协议

PaperWright 的确定性合并覆盖常见版式，但不同期刊的栏排布不同，规则无法保证
每篇都拼全。本协议让**任何文本或视觉模型**用同一个可校验的方式补上规则没
拼到的段落：模型只负责"识别"和"声明"，拼接正确性由校验器保证。模型无关，
不假设具体厂商或工具。

## 何时使用

转换后的 `article.md` 或 Article Model 里出现以下**断句信号**时：

- 一个段落以小写字母开头的独立块（段落不应以小写开头 → 它是上一段的续行）；
- 一个块以非句末标点（`.,:;?!`）或字母结尾，紧邻下一块以小写开头；
- 跨页断句：上一页末尾无句号，下一页开头小写；
- 视觉确认：版面图显示两段位于同一逻辑栏位的连续行（跨列底部→顶部、跨页）。

这些信号不需要规则预判 —— 模型读文本或看页面即可发现。规则已拼对的
由确定性合并处理，模型只需处理剩余的。

## 协议：一次合并一个相邻对

一个操作只能合并**阅读顺序相邻的两个 body 块**：

```json
{
  "op": "join-blocks",
  "target_block_ids": ["blk_<24-hex>", "blk_<24-hex>"],
  "reason": "Same paragraph split at a column boundary."
}
```

校验器强制（全部满足才通过，任一违反即拒绝）：

1. 两个块都是 `kind=body`、`editable=true`；
2. **同一页**（`page` 相同）；
3. **正向阅读顺序相邻**（后块 `order` = 前块 `order + 1`，禁止反向 pair）；
4. 两块都**不参与关系**（不连接 Figure/Caption 等）；
5. 文本证据：前块**不以 `.!?:;` 结尾**，且后块**首字符是小写字母** ——
   这是"续行"的可证明信号，防止把两个独立段落拼在一起；
6. 拼接 = 前块文本 + 空格（或前块以连字符结尾时无空格）+ 后块文本，零改写。

## 模型应该做什么 / 不应该做什么

- **做**：扫描 Article Model 的 blocks，找出满足信号、校验规则也接受的相邻
  body 对，为每一对构造一个 `join-blocks` 操作。用真实页面确认"确实是一段"
  时可以看 `page.png`（视觉模型）。
- **不做**：不要改写文本、不要删字、不要改前块结构。协议只允许"纯拼接"。
  前块的稳定 ID、source spans、顺序保持，后块被移除（物理层仍可溯源）。
- **链式拼接**（A→B→C 是同一段）：一次只能声明一对。先拼 A+B 应用，
  再对结果拼 (AB)→C；不要在一个 review 里让同一块出现在两个操作中
  （校验器拒绝）。

## 命令流程

```bash
paperwright text-prepare <article-model.json> text-task.json
# 模型扫描 text-task.json 的 blocks，构造 text-review.json
paperwright validate-text-review text-review.json --task text-task.json
paperwright text-apply <article-model.json> text-task.json text-review.json article-model.reviewed.json
```

`validate-text-review` 输出 `valid` 才能进入 `text-apply`；被拒的操作会给出原因，
模型据此修正（改目标块、换对、或放弃）。

## 规则与协议都覆盖不到时：L3 程序合成

三列、侧栏、浮动注记等复杂版式，若相邻对/小写证据不满足，但仍能从页面看出
是同一段，可用 L3 程序合成桥（`tools/run_text_synthesize.py`）：

1. 模型只写**受限 DSL 脚本**（ast 白名单、只读 `api.*` 原语、无 import/反射/IO）；
2. 脚本通过 `emit_join` 声明意图，守恒校验 + `validate-text-review` 仍强制
   "纯拼接、零改写"；
3. 脚本全文、task/Article Model/source/review 哈希一并写入
   `synthesize-run.json`；`text-package --synthesis-run` 将其落入
   `_paperwright/06-text-review/` 并写 manifest v0.11，`validate-text-package`
   会重新执行脚本证明同一输入重放得到同一输出。

无法用 DSL 表达且仍要保守处理时，才退回人工检查；不要绕过校验器手改
Article Model。
