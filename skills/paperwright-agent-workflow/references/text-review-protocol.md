# Text review protocol v0.2

Read the whole `text-task.json`. Return one JSON object and no Markdown fence.
Copy `source_sha256` and `article_model.sha256` exactly. Compute or obtain
`task_sha256` from `paperwright validate-text-task`; never guess a hash.

```json
{
  "contract_version": "paperwright-text-review-v0.2",
  "task_sha256": "64 lowercase hex characters",
  "source_sha256": "copied from task",
  "article_model_sha256": "copied from task.article_model.sha256",
  "reviewer": "model or agent identity",
  "operations": []
}
```

## replace-markdown

Each block may be replaced at most once:

```json
{
  "op": "replace-markdown",
  "block_id": "copied editable block id",
  "expected_markdown_sha256": "copied block markdown_sha256",
  "change_mode": "format-only",
  "markdown": "replacement single-line Markdown",
  "reason": "concise evidence-based reason"
}
```

- `format-only`: normalized visible text must be unchanged, for example adding
  or removing Markdown emphasis or normalizing whitespace.
- `dehyphenation`: only remove a native-text line-break artifact such as
  `multi- modal` to `multimodal`; no other visible character may change.

## join-blocks

Two adjacent body blocks may be spliced as one paragraph:

```json
{
  "op": "join-blocks",
  "target_block_ids": ["blk_...", "blk_..."],
  "reason": "Same paragraph split at a column boundary."
}
```

The validator recomputes and enforces every condition: both blocks are `body`,
editable and unrelated; same page; the second block's `order` equals the
first block's `order + 1` (reversed pairs are rejected); the first block does
not end with `. ! ? : ;`; the second block starts with a lowercase letter. The
result is pure concatenation (one space, or no space after a trailing hyphen or
slash). The first block keeps its stable ID; the second is removed from the
public model but stays traceable in `physical_document.json`.

Never rewrite text inside a join. Chain A→B→C by applying A+B first, then
(AB)+C; one review must not reference the same block in two operations.

When the declared operations are not enough (three-column, sidebar, floating
notes), use the restricted L3 synthesis bridge
(`tools/run_text_synthesize.py`). Its `synthesize-run.json` records the script
and hashes; `text-package --synthesis-run` writes manifest v0.11 and
`validate-text-package` replays the script to prove the same output.

## Hard boundaries

- Do not edit blocks whose `editable` value is false, or visual slots.
- Do not change IDs, kinds, orders, source spans, asset paths, Figure/Table
  slots, or task fields.
- Do not split or reorder blocks; do not delete text or change Markdown
  heading levels.
- Do not correct scientific claims, spelling, punctuation, citations,
  equations, or captions from memory.
- Use an empty `operations` array when no permitted cleanup is justified.
