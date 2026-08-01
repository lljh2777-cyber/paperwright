---
name: paper2md-install
description: Download, clone, install, upgrade, or verify Paper2MD from its source repository in an isolated Python environment. Use for Paper2MD setup requests, Git or ZIP acquisition, Windows or Linux installation, CLI discovery failures, dependency/version checks, or preparing a source checkout for conversion or development.
---

# Install Paper2MD

Install from the source checkout and verify the executable before handing it off.

## Workflow

1. Read repository-level `AGENTS.md` instructions when present.
2. Read the checkout's `README.md`, `pyproject.toml`, and `docs/SUPPORT_MATRIX.md`; treat them as the current source of truth rather than versions copied into prompts.
3. Choose Git clone when Git is available. Otherwise direct the user to GitHub's source ZIP. Do not claim that a public PyPI release exists.
4. Confirm that the checkout root contains `pyproject.toml`, `src/paper2md/`, and `README.md`.
5. Select a 64-bit Python version allowed by `project.requires-python` in `pyproject.toml`. Use the interpreter required by local agent instructions when specified.
6. Create a project-local virtual environment and install the checkout with `python -m pip install .`. Use `python -m pip install -e .` only when the user is preparing to contribute.
7. Request any authorization required by the host before downloading dependencies. Do not fetch binaries from unofficial mirrors.
8. Verify both `paper2md --version` and `paper2md --help`. If the console script is not discoverable, verify with `python -m paper2md --help` from the same environment.
9. Report the selected interpreter, installed Paper2MD version, verification result, and any unverified platform caveat.

## Guardrails

- Preserve existing checkouts, virtual environments, input PDFs, and generated outputs unless the user explicitly asks to replace them.
- Do not install into the system interpreter when an isolated environment is available.
- Do not select `pdfbox`; the current repository keeps it only as an unavailable interface boundary.
- Do not promise OCR, scanned-PDF support, GUI, Web/API service, container images, or support for platforms marked unverified.
- Keep `LICENSE` and `NOTICE` when redistributing the source or a derivative.

## Platform commands and diagnosis

Read [references/install-and-diagnose.md](references/install-and-diagnose.md) for Windows/Linux command templates and common installation failures.
