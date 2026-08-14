# Text review protocol v0.1

Read the whole `text-task.json`. Return one JSON object and no Markdown fence.
Copy `source_sha256` and `article_model.sha256` exactly. Compute or obtain
`task_sha256` from `paperwright validate-text-task`; never guess a hash.

```json
{
  "contract_version": "paperwright-text-review-v0.1",
  "task_sha256": "64 lowercase hex characters",
  "source_sha256": "copied from task",
  "article_model_sha256": "copied from task.article_model.sha256",
  "reviewer": "model or agent identity",
  "operations": [
    {
      "op": "replace-markdown",
      "block_id": "copied editable block id",
      "expected_markdown_sha256": "copied block markdown_sha256",
      "change_mode": "format-only",
      "markdown": "replacement single-line Markdown",
      "reason": "concise evidence-based reason"
    }
  ]
}
```

Use `format-only` only when normalized visible text is unchanged, for example
adding or removing Markdown emphasis or normalizing whitespace. Use
`dehyphenation` only to change a native-text line-break artifact such as
`multi- modal` to `multimodal`; it cannot alter any other visible characters.

Do not edit blocks whose `editable` value is false. Do not change IDs, kinds,
orders, asset paths, Figure/Table slots, or task fields. Do not merge, delete,
split, or reorder blocks, and do not change Markdown heading levels. Do not correct scientific claims, spelling,
punctuation, citations, equations, or captions from memory. Use an empty
`operations` array when no permitted cleanup is justified.
