---
name: paper2md-contribute
description: Understand, modify, test, review, and contribute to the Paper2MD source repository. Use for bug fixes, features, refactors, documentation, schemas and manifest changes, layout heuristics, deterministic PDF reconstruction, test failures, release validation, code review, or preparing commits and pull requests for Paper2MD.
---

# Contribute to Paper2MD

Make scoped, evidence-backed changes while preserving Paper2MD's deterministic and provenance-aware boundaries.

## Start with repository truth

1. Read repository-level `AGENTS.md` and obey its execution, deletion, and Git rules.
2. Inspect `git status --short --branch` before changing files. Preserve unrelated user changes.
3. Read `README.md`, `docs/ARCHITECTURE.md`, and the modules/tests nearest the requested behavior.
4. Read [references/project-map.md](references/project-map.md) only for architectural routing, [references/contracts.md](references/contracts.md) for schema/version work, and [references/validation.md](references/validation.md) before testing.
5. Confirm the request is a diagnosis, review, or implementation. Do not modify code for a read-only diagnosis unless asked.

## Preserve product invariants

- Keep the core local-first and deterministic. Do not add implicit LLM, external API, cloud OCR, or telemetry calls.
- Treat born-digital native PDF text as the source of text. A visual reviewer may decide geometry and roles, but must not author article text.
- Preserve provenance, source hashes, path safety, atomic output, stable ordering, and explicit nonzero failures.
- Prefer conservative degradation over invented tables, equations, figures, captions, or reading order.
- Keep output compatibility deliberate. Package versions and data-contract versions evolve independently.
- Do not broaden platform or backend support claims without evidence.

## Implement

1. Reproduce the issue or establish the expected contract with the smallest relevant test.
2. Place responsibilities in the narrowest existing module; avoid adding more unrelated rules to large orchestration modules.
3. Add or update focused tests alongside the change. Use runtime-generated minimal PDFs or existing fixture factories rather than committing third-party papers.
4. Update user docs when CLI behavior, support, output structure, configuration, or limitations change.
5. Update schemas, model parsing, fixtures, migrations, and contract documentation together when a data contract changes.
6. Run targeted tests first, then the validation tier proportional to risk.
7. Review `git diff --check`, the final diff, and repository status before committing.
8. Commit and push only when authorized by the user and host policy. Never discard unrelated changes to obtain a clean tree.

## Review standards

- Treat deterministic structural failures, data loss, unsafe paths, hash bypasses, overwrite behavior, and contract incompatibility as high priority.
- Distinguish heuristic quality warnings from hard correctness failures.
- Require visual evidence for semantic layout claims; candidate counts or successful execution alone do not prove correct reading order.
- Keep private compatibility wrappers only when they protect a real supported boundary; otherwise favor public contracts and focused modules.

## Handoff

Report the behavior changed, files affected, tests executed with exact outcomes, remaining limitations, and Git commit/push state. Do not claim full validation if only targeted tests ran.
