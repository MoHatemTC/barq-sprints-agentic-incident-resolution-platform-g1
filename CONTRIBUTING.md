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

Tests must pass on a clean clone with no `.env`, **and must also pass with an
arbitrary `.env` present** — unit tests run with `.env` loading disabled
(`app/core/config.py`), so nothing in a developer's local `.env` can leak into or
break the suite. `tests/conftest.py` backfills fake ServiceNow settings for ordinary
runs only; if you add a required setting, add it there too or you will break everyone else's checkout.

Live ServiceNow tests require an explicit process-level opt-in and should be run separately:

```bash
SERVICENOW_LIVE_TESTS=1 uv run pytest tests/clients/test_servicenow_integration.py
```

The required ServiceNow credentials and `SERVICENOW_TEST_INCIDENT_SYS_ID` may come
from `.env` or the process environment; process values take precedence. Missing or
invalid live configuration skips the module instead of starting a partial live run.

## Commit messages

Explain **why**, not what — the diff already shows what. State the problem, then the
fix. If you are reverting or working around something, say what you tried first.

Commit messages end with the last line of the body. No trailers, no generated-by
footers, no co-author lines that were not real human co-authors.

## ServiceNow work

- Every artifact belongs in scope `x_2215032_ai_inc_0`. **Nothing in Global.**
- Scripts compare **internal** choice values (`in_progress`), never display labels.
- Incident columns carry the `x_2215032_ai_inc_0_ai_` prefix. Field types and intended
  write permissions are in [`docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md`](docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md)
  — that file is the contract every workstream writes against, so changing it affects
  the backend, the graph and the audit trail.
- Export update sets through the official SDK and do not hand-edit the XML. Verify a
  clean preview and commit on a second instance before calling it done.

## Security

Read [SECURITY.md](SECURITY.md). The short version: no admin credentials anywhere, no
secrets in logs, and permission matrices record observed results rather than intended
ones.

## Reviews

**Nothing reaches `main` without an approving review from someone other than the author.**
`main` is protected and enforces this, including for admins. Open a pull request and wait
for a human — even for a one-line change, even close to a deadline.

**Never self-merge a change to security or governance.** That means anything under
`.github/`, `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, or any ServiceNow ACL or role
in an update set. These define how the repository and the platform protect themselves, so
a second person reads them by rule, not by preference.

Open pull requests with the checklist intact:

```
gh pr create --template .github/pull_request_template.md
```

A body passed with `--body` or `--body-file` replaces the template, so if you use one,
paste the checklist into it. The template pre-fills automatically only in the web UI.

Copilot reviews every pull request automatically. Address its findings or say why you
disagree — do not merge over an unaddressed one. Requirement-level review belongs to
the workstream owner listed in [CODEOWNERS](.github/CODEOWNERS).

### Dismissing a change request

**Only the reviewer who requested changes may dismiss their own review.** If someone
else's review is blocking you, push the fix and re-request review. Do not dismiss it,
and do not merge around it. A dismissal marks a concern as resolved on the author's
word, which is the one thing review exists to avoid.

This rule exists because it was broken. On 2026-09-13, under deadline pressure, three
change requests were dismissed and the pull requests merged within seconds. #61 merged
nine seconds after its gate was dismissed, closed #35 with its first criterion unmet,
and turned `main` red for eight minutes. #32 and #30 merged the same way, and between
them landed twelve issues that are still open. See #101, #102 and #103.

**The one exception, and its conditions.** Some reviews come from an automated reviewer
that cannot be asked to look again. When that reviewer is blocking and the findings are
genuinely fixed, a dismissal is the only route forward. It is permitted only when all
of these hold:

- **Every finding is verified individually, by command, and the output is quoted in the
  dismissal message.** Not "addressed" — the actual check, so a reader can re-run it.
- **The dismissal message says who dismissed it and why it was not a re-review.**
- **If the pull request now contains commits by the person dismissing, the message says
  so.** Approving a branch you have pushed to is not an independent review, whoever
  opened it.

A dismissal that cannot meet those conditions is a merge waiting to be reverted.

**This exception was used on 2026-09-16** for #77, #78, #79 and #80. The requesting
reviewer is an automated account the team was instructed not to re-summon, all thirteen
findings across the four pull requests were verified by command first, and each
dismissal records the evidence and the fact that three of the four branches carried
commits by the person dismissing. Recorded here rather than left in the pull request
history, so the precedent is visible and the conditions are the reason it was
acceptable — not the deadline.
