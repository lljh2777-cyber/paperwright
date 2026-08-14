# Layout regression workspace

This directory is for checking whether PaperWright layout fixes generalize across
papers and publishers.

Local structure:

- `inputs/`: source PDFs used only on the local machine;
- `runs/`: generated review bundles and conversion outputs;
- `cases/`: small, copyright-safe JSON expectations that may be committed.

PDFs and generated outputs must not be committed. A useful regression case
records the source SHA-256, page number, expected logical Figure/Table count,
expected caption grouping, and any page-furniture exclusions. Prefer synthetic
fixtures for automated tests and use local real papers only for visual
acceptance checks.

The current Novae acceptance target is PDF page 5: one Figure containing panels
a–i, one two-column caption, and excluded journal header/footer rules.
