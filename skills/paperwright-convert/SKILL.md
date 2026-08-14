---
name: paperwright-convert
description: Use PaperWright to reconstruct born-digital scientific PDFs as Markdown, images, manifests, and provenance. Use for single or batch PDF conversion, choosing direct versus hybrid reviewed layout, preparing multimodal or human review bundles, validating final-layout JSON, applying reviewed layouts, inspecting evidence, or diagnosing conversion failures.
---

# Convert with PaperWright

Choose the least complex workflow that meets the requested layout fidelity, then validate the produced artifacts.

## Ask before running

Before starting a conversion, ask the user about every unresolved choice that
materially changes fidelity, output scope, storage, privacy, or failure handling.
Do not silently turn CLI defaults into user decisions.

- Do not re-ask for choices already stated by the user or safely discovered from
  the current files and environment.
- Ask no more than three short, grouped questions at a time. Put the recommended
  option first and state its practical consequence.
- Resolve the workflow and output destination first. Then ask only the options
  that apply to direct, hybrid, or batch conversion.
- If the user explicitly delegates the choices, use the recommended settings,
  state them before execution, and continue without another confirmation round.
- Pause for an answer when a choice can alter document fidelity, omit content,
  copy the source PDF, call an external service, or change batch failure handling.

At minimum, resolve the workflow, output destination, workflow-specific visual
handling, and final package policy. Read
[references/options-and-questions.md](references/options-and-questions.md) before
planning or running a conversion; it maps the CLI options to staged user
questions and recommended settings.

## Preflight

1. Read repository-level `AGENTS.md` instructions when present.
2. Verify the installed CLI with `paperwright --version` and `paperwright --help`; use `python -m paperwright` from the same environment as a fallback.
3. Confirm that each input is a born-digital PDF with a native text layer. State that PaperWright does not perform OCR when the input is scanned or image-only.
4. Resolve input and output paths explicitly. Use a new output directory: PaperWright intentionally refuses to overwrite existing output.
5. Read the current command help before using optional flags. Do not infer behavior from an older manifest version.

## Choose a workflow

- Use **direct conversion** for ordinary born-digital papers when deterministic extraction and baseline double-column ordering are sufficient.
- Use **hybrid reviewed layout** for complex columns, spanning Figure/Table regions, ambiguous captions, page furniture, or when semantic reading order matters. Its default `visual-direct` mode first requires confirmation of the coarse Content ROI, then requires a human or multimodal visual AI to inspect the clean `page.png`, draw final regions inside that ROI, and create each page's structured `final-layout.json`. Do not infer final Figure/Table bounds from rule-generated candidates.
- Use **batch conversion** only for independent direct conversions. It scans one directory level and never makes hybrid review decisions.

Read [references/direct-and-batch.md](references/direct-and-batch.md) for direct commands and [references/hybrid-review.md](references/hybrid-review.md) for the review protocol.

## Review output

1. Check the command exit status before inspecting artifacts.
2. For direct output, confirm `article.md`, `images/`, `manifest.json`, and `physical_document.json` as applicable.
3. For hybrid output, inspect `article.md`, every Figure/Table image,
   `_paperwright/article-model.json`, `_paperwright/reader.json`, and
   `_paperwright/05-validation/validation-report.md` when retained. Run
   `paperwright validate-article-model <article-model-json-path>` before handing
   the package off to a reader.
4. When the user requests separate visual and text reviewers, load
   `paperwright-agent-workflow` after hybrid output validation. Give its text
   reviewer only the generated `text-task.json`, never page images or an
   unrestricted Article Model.
5. **Check cross-block paragraph joins.** Deterministic merging covers common
   layouts but cannot generalize to every column arrangement. Inspect the
   Article Model for split-paragraph signals — a body block starting lowercase,
   or a block ending without sentence punctuation followed by a lowercase-start
   block on the same page — and repair them with the model-agnostic `join-blocks`
   protocol. Read [references/join-blocks-protocol.md](references/join-blocks-protocol.md)
   before running a text review; it defines when to join, the exact operation
   schema, what the validator rejects, and when a conservative one-off script
   is acceptable instead. Never rewrite text inside a join: the protocol is
   pure concatenation enforced by the validator.
6. Treat warnings as review leads, not automatic proof of failure. Treat deterministic `FAIL` results as blocking.
6. Report limitations plainly: no OCR, no semantic table reconstruction, no formula-to-LaTeX conversion, and conservative handling of uncertain figures.

## Safety and provenance

- Never ask the visual reviewer to transcribe, rewrite, summarize, or directly generate Markdown from the page image.
- Never let the reviewer invent PDF element IDs; `source_element_ids` must remain empty in AI-authored layout JSON.
- Preserve the original PDF and review bundle until output validation succeeds.
- Do not mutate or reuse a prepared review bundle with a different source PDF or extraction profile; PaperWright validates hashes and recorded extraction decisions.
- Do not call external AI services unless the user explicitly authorizes that integration and the host policy permits it. PaperWright itself makes no such call.

## Troubleshooting

Read the checkout's `docs/TROUBLESHOOTING.md` for current error categories. Prefer the CLI's structured error and the evidence report over guesses.
