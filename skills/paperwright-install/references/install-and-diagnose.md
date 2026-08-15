# Installation and diagnosis

## Acquire the source

```bash
git clone https://github.com/lljh2777-cyber/paperwright.git
cd paperwright
```

If the README's PyPI path is selected, verify `pip install paperwright`
succeeds in the chosen environment; if the index has not published the current
Alpha version, fall back to the source checkout.

Without Git, download the repository ZIP from GitHub, extract it, and enter the directory containing `pyproject.toml`.

## Windows PowerShell

Use a supported interpreter discovered on the host; `3.12` is an example, not a hard-coded requirement.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
paperwright --version
paperwright --help
```

If activation is restricted, run the environment's interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m paperwright --help
```

## Linux

Install the distribution's `python3-venv` and `python3-pip` packages first when absent.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
paperwright --version
paperwright --help
```

## Diagnose failures

- `pyproject.toml` not found: change to the repository root.
- `paperwright` not found: activate the same environment used for installation or use `python -m paperwright`.
- incompatible Python: re-read `project.requires-python` in `pyproject.toml` and select a permitted 64-bit interpreter.
- dependency download failure: confirm network/proxy/system time and the host's authorization policy; the repository does not provide an offline bundle.
- `backend_unavailable`: confirm the default `pdfium` backend and install the checkout's pinned dependencies.
- unsupported platform: state the limitation from `docs/SUPPORT_MATRIX.md`; do not silently present an unverified platform as supported.
