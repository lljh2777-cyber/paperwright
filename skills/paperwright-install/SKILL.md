---
name: paperwright-install
description: Download, clone, install, upgrade, or verify PaperWright from its source repository in an isolated Python environment. Use for PaperWright setup requests, Git or ZIP acquisition, Windows or Linux installation, CLI discovery failures, dependency/version checks, or preparing a source checkout for conversion or development.
---

# Install PaperWright

Install from the source checkout and verify the executable before handing it off.

## Confirm installation choices

Before downloading or changing a Python environment, ask the user about any
unresolved material choice: use an existing checkout or acquire a new one, choose
Git clone or source ZIP when both are possible, select the target interpreter or
virtual-environment location, and choose a regular install for use or an editable
install for contribution. Recommend a project-local virtual environment and a
regular install for ordinary use.

Do not ask for information already available from the host, repository
instructions, or the user's request. Group at most three short questions, explain
the recommended choice, and wait before replacing an existing environment,
downloading dependencies, or installing editable source. If the user delegates
the decisions, state the selected checkout, interpreter, environment, and install
mode before proceeding.

## Workflow

1. Read repository-level `AGENTS.md` instructions when present.
2. Read the checkout's `README.md`, `pyproject.toml`, and `docs/SUPPORT_MATRIX.md`; treat them as the current source of truth rather than versions copied into prompts.
3. Use the acquisition and installation choices confirmed above. If the README offers the PyPI package path, verify `pip install paperwright` before reporting success; otherwise install from source. Do not present the source Alpha as a stable public release.
4. Confirm that the checkout root contains `pyproject.toml`, `src/paperwright/`, and `README.md`.
5. Select a 64-bit Python version allowed by `project.requires-python` in `pyproject.toml`. Use the interpreter required by local agent instructions when specified.
6. Create a project-local virtual environment and install the checkout with `python -m pip install .`. Use `python -m pip install -e .` only when the user is preparing to contribute.
7. Request any authorization required by the host before downloading dependencies. Do not fetch binaries from unofficial mirrors.
8. Verify both `paperwright --version` and `paperwright --help`. If the console script is not discoverable, verify with `python -m paperwright --help` from the same environment.
9. Report the selected interpreter, installed PaperWright version, verification result, and any unverified platform caveat.

## Guardrails

- Preserve existing checkouts, virtual environments, input PDFs, and generated outputs unless the user explicitly asks to replace them.
- Do not install into the system interpreter when an isolated environment is available.
- Do not select `pdfbox`; the current repository keeps it only as an unavailable interface boundary.
- Do not promise OCR, scanned-PDF support, GUI, Web/API service, container images, or support for platforms marked unverified in `docs/SUPPORT_MATRIX.md`.
- Keep `LICENSE` and `NOTICE` when redistributing the source or a derivative.

## Platform commands and diagnosis

Read [references/install-and-diagnose.md](references/install-and-diagnose.md) for Windows/Linux command templates and common installation failures.
