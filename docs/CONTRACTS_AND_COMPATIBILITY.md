# Contracts and compatibility

Read `docs/ARCHITECTURE.md`, the relevant model, and the corresponding schema before changing a contract.

## Rules

1. Keep package version and serialized contract versions independent.
2. Change a contract version only for an intentional serialized compatibility boundary.
3. Update the model serializer/parser, `src/paperwright/schemas/*.schema.json`, manifest validation, fixtures, tests, architecture/support docs, and migration notes together when applicable.
4. Preserve reading support for documented older contracts unless the change explicitly removes it and includes migration guidance.
5. Keep canonical JSON deterministic: UTF-8, stable key ordering/separators, finite numbers, normalized text, and stable list ordering where the contract requires it.
6. Preserve source hashes, task hashes, extraction-profile records, and evidence-file hashes across the prepare/apply boundary.
7. Reject unknown or unsafe configuration fields rather than silently accepting them.

## Current sources of truth

- package version: `pyproject.toml` and `src/paperwright/__init__.py`
- physical document model: `models.py` and `schemas/physical_document.schema.json`
- multi-provider source evidence: `source_evidence.py` and
  `schemas/source_evidence.schema.json`
- restricted paper decisions and source-element tree: `paper_recipe.py`,
  `schemas/paper_recipe.schema.json`, and `schemas/article_tree.schema.json`
- canonical final article tree: `article_tree.py` and
  `schemas/final_article_tree.schema.json`
- layout task/final layout: `layout_models.py` and their schemas
- manifest writers/readers: `manifest.py`, `writer.py`, and `layout_writer.py`
- reader interoperability: `reader.py`, `reader_contract.py`, and
  `schemas/reader.schema.json`
- source-preserving text review: `text_review.py`,
  `schemas/text_task.schema.json`, and `schemas/text_review.schema.json`
- compatibility statements: `docs/ARCHITECTURE.md`, `docs/SUPPORT_MATRIX.md`, and migration documents

Never copy version values from this document into code. Read them from the checkout being modified.
