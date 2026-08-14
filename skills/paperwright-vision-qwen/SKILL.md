---
name: paperwright-vision-qwen
description: Use qwen-mm-plugins visual MCP tools as PaperWright's optional visual reviewer — Content ROI proposals, visual-direct final-layout regions, join-blocks paragraph-splice confirmation, and figure/caption checks. Only usable when the qwen-mm-plugins MCP servers are connected and a DashScope key is configured; PaperWright core and the other skills work without it.
---

# Visual review with qwen-mm-plugins

This skill binds PaperWright's **visual review role** to Qwen's multimodal MCP
tools. It does not change PaperWright's core or its contracts: every visual
judgment is mapped to an existing structured contract and re-validated by
PaperWright's own validators. The model is interchangeable — any visual model
that fills the same contract works; this skill is the concrete qwen wiring.

## When this skill applies

- The user wants hybrid reviewed layout (`visual-direct` mode) and has a
  multimodal reviewer available.
- You need to confirm a split-paragraph splice (is fragment A the continuation
  of fragment B?) before authoring a `join-blocks` operation.
- You want an optional figure/caption consistency check on converted output.

When qwen-mm-plugins MCP tools are **not** connected (or no DashScope key is
configured), this skill is unavailable: fall back to the human visual review
path in `paperwright-convert` and `paperwright-agent-workflow`. Never pretend a
visual check happened.

## Prerequisites

1. qwen-mm-plugins installed and its MCP servers connected (check with a
   `vision_chat` probe on any image before starting).
2. A DashScope API key for the account's workspace endpoint. Do not echo the
   key into chat or transcripts.
3. The source page images you need: `layout-prepare` already emits `page.png`
   per page; use them.

## Role boundary

- Decide **geometry and semantics only**: content ROI, region roles, reading
  order, whether two text fragments are one paragraph.
- Never transcribe, rewrite, or summarize paper text.
- Never invent PDF element IDs. If a judgment must be expressed as layout JSON,
  leave `source_element_ids` empty and let PaperWright bind them.
- Report honestly when the model is unsure or the image is ambiguous.

## Workflow

Load the detailed protocol in
[references/visual-review-protocol.md](references/visual-review-protocol.md)
before running any visual step. In short:

1. **Probe**: confirm `vision_chat` works on one `page.png`.
2. **Content ROI** (before `layout-prepare` confirm): ask the model for the
   page's usable content rectangle, map it to `content-roi.json`, and keep the
   existing human confirmation step.
3. **visual-direct regions**: ask the model for each region's bounds and role
   (body / heading / caption / figure / table / excluded furniture) plus
   reading order; emit `final-layout.json` with empty `source_element_ids`.
4. **Splice confirmation** (before authoring `join-blocks`): ask whether the
   two fragments are the same paragraph; only author the operation when the
   visual answer agrees with the text evidence the validator requires.
5. **Figure/caption check**: verify each rendered figure matches its caption.

## Validate everything

Every structured artifact must pass PaperWright's own validator before use:

```bash
paperwright validate-final-layout FINAL_LAYOUT_JSON --task LAYOUT_TASK_JSON
paperwright validate-text-review TEXT_REVIEW_JSON --task TEXT_TASK_JSON
```

The validators — not the visual model — are the source of truth. If a visual
judgment fails validation, fix the artifact or fall back to a human reviewer;
do not weaken the contract to fit the model output.

## Troubleshooting

- MCP tools missing → reinstall qwen-mm-plugins and reconnect; this skill is
  intentionally a no-op without them.
- 403 / model-not-allowed → the DashScope key or workspace model list changed;
  report it and fall back to human review.
- Model confidently wrong about a splice → trust the text evidence + validator,
  not the model; when they disagree, do not author the join.
