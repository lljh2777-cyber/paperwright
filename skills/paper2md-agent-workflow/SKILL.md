---
name: paper2md-agent-workflow
description: Coordinate Paper2MD conversion with separate visual-layout and pure-text review agents. Use when a main agent must prepare a born-digital scientific PDF, delegate page geometry to a multimodal sub-agent, delegate source-preserving Markdown cleanup to a text-only sub-agent, validate both structured reviews, and preserve hashes, provenance, stable Reader identities, and user-selected conversion options.
---

# Coordinate Paper2MD agents

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

External service authorization does not relax Paper2MD validation or provenance
rules. Prefer local agents when privacy is unresolved.

## Run the visual stage

1. Read current `paper2md --help`; do not infer flags from an older contract.
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

1. Validate `_paper2md/article-model.json` and its current Markdown, Reader, and
   assets with `paper2md validate-article-model`.
2. Generate a task pinned to that exact model:

   ```bash
   paper2md text-prepare ARTICLE_MODEL_JSON TEXT_TASK_JSON
   ```

3. Give the text sub-agent only `text-task.json` and the protocol in
   [references/text-review-protocol.md](references/text-review-protocol.md).
   Do not give it page images or permission to rewrite the paper.
4. Require one `text-review.json`. Validate it against the exact task:

   ```bash
   paper2md validate-text-review TEXT_REVIEW_JSON --task TEXT_TASK_JSON
   ```

5. Apply it to a new model path; never overwrite the source model:

   ```bash
   paper2md text-apply ARTICLE_MODEL_JSON TEXT_TASK_JSON TEXT_REVIEW_JSON REVIEWED_MODEL_JSON
   ```

The v0.1 text protocol permits visible-text-preserving Markdown formatting and
strict dehyphenation only. It rejects visual-slot edits, semantic rewrites,
stale hashes, duplicate block edits, block merging, deletion, and reordering.
The reviewed model is a separate artifact; do not silently replace the original
package projections.

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
State explicitly that Paper2MD itself made no implicit model or network call.
