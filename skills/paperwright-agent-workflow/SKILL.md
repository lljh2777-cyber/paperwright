---
name: paperwright-agent-workflow
description: Coordinate PaperWright conversion with separate visual-layout and pure-text review agents. Use when a main agent must prepare a born-digital scientific PDF, delegate page geometry to a multimodal sub-agent, delegate source-preserving Markdown cleanup to a text-only sub-agent, validate both structured reviews, and preserve hashes, provenance, stable Reader identities, and user-selected conversion options.
---

# Coordinate PaperWright agents

Keep the main agent responsible for user choices, task hashes, validation, and
final handoff. Give each reviewer only the evidence needed for its role; never
ask either reviewer to produce a finished `article.md` directly.

## Resolve choices

Ask no more than three short questions at a time and do not repeat answered
choices. Resolve:

- input PDF and a new output path;
- direct versus hybrid reviewed layout; use hybrid for this two-reviewer flow;
- extraction profile (`standard` recommended), evidence level (`standard`
  recommended), reference policy (`keep` recommended), and whether the source
  PDF may be copied;
- whether an external AI service may receive page images or extracted text.

External service authorization does not relax PaperWright validation or provenance
rules. Prefer local agents when privacy is unresolved.

## Run the visual stage

1. Read current `paperwright --help`; do not infer flags from an older contract.
2. Run `layout-prepare` once for Content ROI proposals. Require explicit human
   or visual-reviewer confirmation of every ROI before the next preparation.
3. Run `layout-prepare` with the confirmed ROI and `visual-direct` mode.
4. Delegate each page independently when sub-agents are available. Give the
   visual reviewer `page.png`, the confirmed ROI, the page task, and its review
   instructions. Require structured `final-layout.json` only.
5. Forbid transcription, Markdown writing, invented PDF element IDs, and text
   read from inside Figure/Table regions. The visual reviewer decides geometry,
   semantic region roles, and reading order only.
6. Validate every final layout against its exact task, then run `layout-apply`.
   Stop on any hash, ROI, schema, or completeness failure.

Do not treat successful schema validation as proof of visual correctness. Inspect
the rendered Figure/Table assets and the final article at least once.

## Run the text stage

1. Validate `_paperwright/article-model.json` and its current Markdown, Reader, and
   assets with `paperwright validate-article-model`.
2. Generate a task pinned to that exact model:

   ```bash
   paperwright text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON
   ```

3. Give the text sub-agent only `text-task.json` and the v0.2 protocol in
   [references/text-review-protocol.md](references/text-review-protocol.md).
   Do not give it page images or permission to rewrite the paper.
4. Require one `text-review.json`. The reviewer may include `replace-markdown`
   cleanup and `join-blocks` operations for split paragraphs; joins are pure
   concatenation that the validator recomputes and enforces. Validate against
   the exact task:

   ```bash
   paperwright validate-text-review TEXT_REVIEW_JSON --task TEXT_TASK_JSON
   ```

5. Optionally inspect a model-only result; never overwrite the source model:

   ```bash
   paperwright text-apply ARTICLE_MODEL_JSON TEXT_TASK_JSON TEXT_REVIEW_JSON REVIEWED_MODEL_JSON
   ```

6. For a deliverable package, derive and validate a new complete package:

   ```bash
   paperwright text-package SOURCE_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_PACKAGE
   paperwright validate-text-package OUTPUT_PACKAGE
   ```

   When the review came from the L3 bridge, pass its run record as well:

   ```bash
   paperwright text-package SOURCE_PACKAGE TEXT_TASK_JSON TEXT_REVIEW_JSON OUTPUT_PACKAGE \
     --synthesis-run synthesize-run.json
   ```

   Keep the manifest v0.9 source package until the manifest v0.10 (or v0.11
   with a synthesis run) derivative passes validation. The output path must
   not already exist.

The v0.2 text protocol permits visible-text-preserving Markdown formatting,
strict dehyphenation, and `join-blocks` same-paragraph splices (pure
concatenation, enforced by the validator). It rejects visual-slot edits,
semantic rewrites, stale hashes, duplicate block edits, arbitrary merging,
deletion, and reordering. When declarative operations cannot express a long-tail
layout, the restricted L3 bridge may author a DSL script instead; its run record
is replayed by `validate-text-package`. `text-apply` produces a separate
model-only artifact. `text-package` rebuilds the Markdown, Reader, model,
manifest, and validation records together without silently replacing the
original package projections.

## Keep agents isolated

- Visual reviewer input: page pixels, ROI, layout task, geometry instructions.
- Text reviewer input: text task JSON only.
- Main agent input: both review outputs, original hashes, CLI results, and user
  choices.

Reject a review if an agent returns prose instead of the required JSON. Do not
repair a structurally invalid review by guessing intent; return the validation
error to that reviewer and request a corrected JSON artifact.

## Handoff

Report the selected options, reviewer identities, exact task/review hashes,
operation counts, validation outcomes, output paths, and remaining limitations.
State explicitly that PaperWright itself made no implicit model or network call.
