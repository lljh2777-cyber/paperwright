# Direct and batch conversion

## Single document

```bash
paperwright convert input.pdf output-dir
```

Enable conservative region rendering only when explicitly needed:

```bash
paperwright convert input.pdf output-dir --region-render-mode auto
```

For targeted diagnosis, `explicit` uses zero-based page indexes and may be repeated:

```bash
paperwright convert input.pdf output-dir \
  --region-render-mode explicit \
  --region-render-page 2
```

## Batch

Choose exactly one source form:

```bash
paperwright batch output-root --input-dir pdf-directory --continue-on-error
paperwright batch output-root --input-file a.pdf --input-file b.pdf
paperwright batch output-root --file-list papers.txt
```

`--input-dir` scans only its first level. A UTF-8 file list resolves relative paths against the list file's directory. `--continue-on-error` continues processing but still returns a nonzero exit status when any document fails.

## Configuration

The precedence is built-in defaults, then strict JSON passed with `--config`, then explicit CLI options. Read `docs/CONFIGURATION.md` before changing backend, workspace-root, limits, or region-render policy.

The `pdfbox` choice is an intentional unavailable boundary. Use the default `pdfium` backend.
