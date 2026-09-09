# Contributing

## Workflow

Branch from `main`, open a pull request, let CI run. Never commit directly to `main`,
and **never force-push a shared branch** — rewriting history orphans open pull
requests, and GitHub cannot reopen them afterwards.

Branch naming follows the type of change: `feat/`, `fix/`, `chore/`, `docs/`.

## Before you push

```bash
just check   # lint, format check, type-check, tests — the same commands CI runs
```

Tests must pass on a clean clone with no `.env`. `tests/conftest.py` injects fake
ServiceNow settings so the suite never depends on local developer state; if you add a
required setting, add it there too or you will break everyone else's checkout.

## Commit messages

Explain **why**, not what — the diff already shows what. State the problem, then the
fix. If you are reverting or working around something, say what you tried first.

Commit messages end with the last line of the body. No trailers, no generated-by
footers, no co-author lines that were not real human co-authors.

## ServiceNow work

- Every artifact belongs in scope `x_2215032_ai_inc_0`. **Nothing in Global.**
- Scripts compare **internal** choice values (`in_progress`), never display labels.
- Incident columns carry the `x_2215032_ai_inc_0_ai_` prefix. Field types and intended
  write permissions are in [`docs/sprint1_field_model.md`](docs/sprint1_field_model.md)
  — that file is the contract every workstream writes against, so changing it affects
  the backend, the graph and the audit trail.
- Export update sets through the official SDK and do not hand-edit the XML. Verify a
  clean preview and commit on a second instance before calling it done.

## Security

Read [SECURITY.md](SECURITY.md). The short version: no admin credentials anywhere, no
secrets in logs, and permission matrices record observed results rather than intended
ones.

## Reviews

Copilot reviews every pull request automatically. Address its findings or say why you
disagree — do not merge over an unaddressed one. Requirement-level review belongs to
the workstream owner listed in [CODEOWNERS](.github/CODEOWNERS).
