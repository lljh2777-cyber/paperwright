# Project map

Read the actual source before editing; this map only routes investigation.

| Area | Primary modules | Primary tests |
|---|---|---|
| CLI and orchestration | `src/paper2md/cli.py`, `api.py`, `batch.py` | `test_cli.py`, `test_paths_api.py`, `test_phase5_alpha.py` |
| Physical model and PDFium | `models.py`, `backends/base.py`, `backends/pdfium.py` | `test_models.py`, `test_pdfium_degenerate.py`, `test_mvp_pipeline.py` |
| Direct reconstruction | `text_reconstruction.py`, `figures.py`, `region_render.py`, `writer.py` | `test_text_reconstruction.py`, `test_phase3_figures.py`, `test_phase4_region_render.py` |
| Hybrid candidate generation | `layout_roi.py`, `layout_candidates.py`, `layout_candidate_features.py`, `raster_layout.py`, `layout_risk.py`, `layout_export.py` | `test_layout_stage_a.py`, `test_layout_stage_b.py`, `test_layout_risk.py`, `test_raster_layout.py` |
| Review and layout application | `layout_models.py`, `layout_review.py`, `layout_writer.py`, `layout_caption.py`, `layout_continuation.py` | `test_layout_stage_c.py`, `test_layout_stage_d.py`, `test_realworld_text_rules.py` |
| Evidence and quality | `manifest.py`, `evidence.py`, `quality.py`, `layout_dataset.py` | `test_manifest.py`, `test_evidence.py`, `test_quality.py`, `test_layout_stage_f.py` |
| Public onboarding | `README.md`, `docs/`, `pyproject.toml` | `test_public_onboarding.py` |

The `schemas/` directory contains shipped JSON schemas. `config/defaults.json` is the built-in configuration source. `tools/` contains repository validation and packaging checks.
