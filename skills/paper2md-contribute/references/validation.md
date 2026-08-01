# Validation

Use the active project environment's Python. If repository-level instructions name a specific interpreter, use it. With an editable install, `PYTHONPATH` may be unnecessary; otherwise set it as shown in `REPRODUCE.md`.

## Targeted tests

Run the narrowest relevant module first:

```bash
python -m unittest tests.test_layout_risk -v
python -m unittest tests.test_cli -v
```

Replace the module with the test nearest the change.

## Full unit suite

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

POSIX shell:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Repository validation

```bash
python tools/generate_fixtures.py --check
python -m compileall -q src tests tools
python tools/check_repo_policy.py --root .
git diff --check
```

Do not regenerate fixtures unless the contract change intentionally requires it and the diff is reviewed.

## Content smoke

Set `PYTHONPATH` to include both `src` and `tests`, using the platform separator, then run:

```bash
python tools/run_content_smoke.py
```

Use this for reconstruction, layout, manifest, determinism, PDFium, or release-facing changes.

## Packaging and batch checks

Run `tools/run_install_checks.py` for packaging, dependency, entry-point, or release changes. Run `tools/run_batch_checks.py` for batch/path/atomic-output changes. Give each tool a new output directory outside the repository as documented in `REPRODUCE.md`.

Use a clean environment with the exact dependencies from `pyproject.toml` when the global environment differs. Report exact pass counts and skipped checks; never summarize a partial run as the full suite.
