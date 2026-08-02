# Options and user questions

Use this decision guide before constructing a Paper2MD command. Ask only about
unresolved choices that apply to the selected workflow. The questions are a
decision checkpoint, not a questionnaire: use one to three short questions per
round and wait for the answer before executing a conversion.

## First round: workflow and destination

Always resolve these unless the request already makes them clear:

1. **Workflow**
   - **Direct (recommended for ordinary born-digital papers):** fastest,
     deterministic baseline conversion without page-by-page visual layout.
   - **Hybrid visual review (recommended for complex layouts):** prepares review
     evidence, requires confirmed Content ROI and final human or multimodal-AI
     page layout, then applies the reviewed regions.
   - **Batch:** independent direct conversions for several PDFs; it cannot make
     hybrid review decisions.
2. **Output destination**
   - Ask for or propose an explicit new directory.
   - Paper2MD refuses to overwrite existing output. If the destination exists,
     ask the user to choose a new path; do not remove or replace it.

Example:

> Which workflow should I use: direct conversion for an ordinary paper
> (recommended), hybrid visual review for complex layout, or batch direct
> conversion? Where should the new output directory be created?

## Direct conversion choices

Ask about region rendering when Figure/Table fidelity matters or the user is
diagnosing visual extraction:

- **`off` (recommended default):** keeps the basic deterministic extraction and
  avoids speculative region crops.
- **`auto`:** enables conservative region rendering for likely visual regions;
  it may improve mixed/vector figures but adds heuristic crops that need review.
- **`explicit`:** renders only user-selected zero-based pages; ask for the page
  indexes and use it for targeted diagnosis.

Do not ask about `--region-render-max-candidates` unless the user selects `auto`
and wants a nondefault cap. Keep `pdfium`; ask about `--backend`, `--config`, or
`--workspace-root` only when the user has a specific compatibility, policy, or
resource constraint. Never recommend the unavailable `pdfbox` boundary.

## Hybrid reviewed-layout choices

Resolve these in stages after the user chooses hybrid review:

1. **Extraction profile**
   - **`standard` (recommended):** fast extraction with explainable escalation
     on risky pages.
   - **`fast`:** least analysis and evidence; use when speed is the priority.
   - **`forensic`:** full object traversal on every page; use for compatibility
     diagnosis or maximum extraction evidence.
2. **Review mode**
   - **`visual-direct` (recommended):** the reviewer draws final regions from the
     clean page image inside the confirmed Content ROI.
   - **`candidate-assisted`:** legacy rule-overlay workflow; use only when the
     user explicitly needs to reproduce or inspect candidate-based review.
3. **Final package policy**
   - Evidence: **`standard` (recommended)**, `minimal`, or `full`.
   - References: **`keep` (recommended)**, `omit`, or `separate`.
   - Source PDF: **do not copy it by default**; ask before
     `--include-source-pdf` because it changes storage and redistribution/privacy
     scope.

Use the profile recorded by `layout-prepare` during `layout-apply` unless the user
deliberately starts a new preparation. Ask about `--preview-scale` or
`--visual-scale` only when the user requests a different review/crop resolution.

Content ROI confirmation is a separate mandatory checkpoint, not a default that
the agent may accept for the user. Ask the user to review every
`content-roi.png`, or obtain explicit authorization for a named visual reviewer,
before marking `content-roi.json` as confirmed. A human or multimodal visual AI
must also review the final layout; schema validity alone is insufficient.

Example after hybrid is selected:

> Should I use the standard extraction profile and visual-direct review
> (recommended), or do you want fast/forensic extraction or the legacy
> candidate-assisted mode? For the final package, should I keep standard
> evidence and references without copying the source PDF (recommended)?

## Batch conversion choices

Resolve:

- **Input selection:** one nonrecursive directory, repeated explicit files, or a
  UTF-8 file list. Show the discovered files or count before execution.
- **Failure handling:** ask whether to stop on the first failed paper (default)
  or use `--continue-on-error` to attempt the rest while still returning nonzero
  if any paper fails.
- **Region rendering:** `off` (recommended baseline) or `auto`; batch does not
  support `explicit` pages.

## When the user delegates the choices

If the user says “use defaults”, “use recommended settings”, or otherwise asks
the agent to decide, proceed with these visible selections:

- ordinary single PDF: direct conversion, region rendering `off`;
- complex-layout single PDF: hybrid, `standard`, `visual-direct`, standard
  evidence, references kept, source PDF not copied;
- batch: explicit discovered input scope, stop on first failure, region rendering
  `off`.

Delegation does not waive Content ROI/final-layout visual review, overwrite
protection, external-service authorization, or validation.
