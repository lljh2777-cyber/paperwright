# Hybrid layout review

## 1. Prepare and confirm Content ROI

Choose an extraction profile deliberately:

- `fast`: native text coordinates plus raster evidence; quickest.
- `standard`: fast analysis with explainable full-object escalation on risky pages; preferred starting point for mixed documents.
- `forensic`: full object traversal on every page; compatibility and maximum evidence.

```bash
paper2md layout-prepare input.pdf roi-review --extraction-profile standard
```

Open every `page-XXXX/content-roi.png`. Ensure the red box includes title, authors, body, footnotes, Figure, Table, and captions while excluding repeated headers, footers, page numbers, and edge decoration. When uncertain, expand the box.

Edit root `content-roi.json` only as required:

- keep coordinates normalized to the original full page;
- set `review_status` to `confirmed`;
- set a nonempty `reviewer`;
- keep every page represented exactly once;
- never change `source_sha256`.

## 2. Prepare per-page layout tasks

```bash
paper2md layout-prepare input.pdf layout-review \
  --content-roi-json roi-review/content-roi.json \
  --extraction-profile standard
```

Use the same selected profile unless a deliberate new preparation is being created. For each `page-XXXX/`, read `review-instructions.md`, inspect `page.png` and `overlay.png`, and use `layout-task.json` for exact geometry and candidate IDs.

## 3. Produce structured review

Create `page-XXXX/final-layout.json` according to `paper2md-final-layout-v0.1` and the page instructions:

- treat candidates as geometric evidence, not as one-to-one final regions;
- follow high-confidence `semantic_review_hints` unless the page image clearly
  contradicts them;
- merge all panels, axes, legends, and labels of one multi-panel Figure into a
  single visual region instead of emitting internal labels as body text;
- merge multi-column caption fragments into one caption region and attach it to
  the corresponding Figure/Table;
- classify and order regions;
- use keep, merge, split, resize, discard, or add actions as justified;
- attach captions to Figure/Table regions;
- account for every candidate through assignment, split, or discard;
- keep non-excluded `order` values consecutive from 1;
- use `unknown` and retain the region when uncertain;
- copy `source_sha256` and `page` from the task;
- set the real reviewer/model name and the instructed `prompt_version`;
- leave `source_element_ids` empty.

Do not transcribe body text, read text inside Figure/Table images, or generate Markdown.

## 4. Validate every page

```bash
paper2md validate-final-layout layout-review/page-0001/final-layout.json \
  --task layout-review/page-0001/layout-task.json
```

Repeat for every page. Fix schema, completeness, action, order, and semantic-role failures before applying.

## 5. Apply and inspect

```bash
paper2md layout-apply input.pdf layout-review output-dir --evidence standard
paper2md validate-reader output-dir/_paper2md/reader.json
```

Use `--evidence minimal` only for compact delivery, `standard` for normal verification, and `full` for audit/training evidence. Use `--include-source-pdf` only when the user wants the source copied into the bundle. Choose `--references keep`, `omit`, or `separate` explicitly when the default `keep` is unsuitable.

Inspect the final Markdown, linked images, Figure/Table crops, reading order, captions,
and validation report. `reader.json` and its public Markdown anchors are retained at
every evidence level; do not remove them from a package intended for reader software.
A schema-valid layout is necessary but does not prove semantic correctness.
