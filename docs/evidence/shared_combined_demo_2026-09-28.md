# Shared BARQ sprint demo audit — 2026-09-28

Checked at 17:14 Cairo (14:14 UTC) against the shared `dev407364` ServiceNow
instance, the deployed EC2 API/worker, PostgreSQL, Redis, Qdrant, GitHub, and
Airtable. These are observations of the deployed `main`, not a claim that the
unmerged S3.4 code is live there.

## Revert and task status

- `git diff --quiet 1340da8 origin/main` exited 0: `main` has the exact tree
  immediately before the #169 merge. The merge and revert remain in history.
- Airtable returned 17 records, all `Done`: G1 S1.1–S1.5, S2.1–S2.6,
  S3.1–S3.5, plus G2 S2.6. It returned no Sprint 4 task.
- PR #158 is open, mergeable, with six successful checks and review required.
  Its current head has not been approved. PR #172 is open and approved; its
  tree equals the reverted #169 tree (`git diff --quiet 69232ce
  origin/enrich-kb-reopened` exited 0).
- Issue #149 remains open. Its integration CI job runs and passes, but making
  checks required in repository settings needs an administrator.

## Fresh shared-path scenarios

Each test incident was created with a `[BARQ combined demo]` marker. The
ServiceNow Business Rule sent the webhook to EC2. API logs show HTTP 202 for
each request. PostgreSQL has one execution per incident, and ServiceNow was
read back with the non-admin integration identity after completion.

| Incident | Execution ID | Scenario | Execution result | ServiceNow read-back |
|---|---|---|---|---|
| `INC0010132` | `6f0f69a0-851f-407e-a033-9504dd1b2cdc` | Priority 3 VPN password change | `succeeded`, `act`, `suggested` | 604-character cited suggestion, confidence 0.95, `awaiting_approval`; no resolution |
| `INC0010133` | `05ec00ec-fb83-4666-8f5b-402f0da72a47` | Priority 1 production outage | `succeeded`, `act`, `escalated_high_risk` | Human review required; no suggestion or resolution |
| `INC0010134` | `e53c9500-ce0a-4961-8cc5-f2d62562ad8c` | Literal prompt injection in incident text | `succeeded`, `act`, `escalated_blocked` | Human review required; no suggestion or resolution |

The normal suggestion cites `[KB0001 v2.0 §Resolution]`. Its worker run took
about 77 seconds, so checking ServiceNow before the execution finishes can
temporarily show `pending` and empty AI fields.

## Knowledge and deployment gap

- The shared ServiceNow target KB has 11 `KB0xxx` records, nine published
  human-captured `KB1xxx` records, and 63 published `KB2xxx` manual records.
- The deployed Qdrant collection is green but holds only 45 points from the
  11 baseline `KB0xxx` versions. It contains no `KB1xxx` or `KB2xxx` points.
  ServiceNow publication alone therefore does not prove shared retrieval.
- The deployed API exposes 15 OpenAPI paths and lacks
  `/api/v1/approvals/pending/{execution_id}`. The full S3.4 interrupt/resume
  and combined S3.5 resume-to-capture flow cannot be demonstrated on EC2 until
  #158 merges and deploys.
- Before this PR's fix, `deploy.yml` reseeded after every merge and
  `seed_qdrant.py` purged articles outside the corpus. This PR now leaves
  knowledge provisioning out of deploys and preserves unrelated points when
  the seed command is run explicitly.

## Verification of the change

- `uv sync --all-extras --dev --locked`, `ruff check .`, `ruff format --check .`,
  and `mypy src` passed.
- Default pytest: 1,320 passed, 30 skipped, 20 integration tests deselected.
  The separate live-service integration run passed 20/20, and the isolated DB
  suite passed 26/26. The 30 default skips were then run explicitly: 18
  database cases passed with the isolated DB URL, 11 ServiceNow integration
  cases passed against the local `dev434590` PDI, and the real-PDF extraction
  case passed using the reference manual at its expected test path. Crash
  recovery, interrupt/resume, guardrails, and tool permissions passed 163/163
  in a focused run.
- A real Qdrant scratch collection held 45 corpus points plus a simulated
  `KB1001` human-captured point. Running `seed_qdrant.py` again left all 46
  points and the captured point's text intact.
- Earlier local-PDI S3.4 proof is in `s34_hitl_demo.json`: the graph parks
  before a write, resumes after approval, and rejects a second decision. That
  proof used `dev434590` and the local stack, not shared EC2.
