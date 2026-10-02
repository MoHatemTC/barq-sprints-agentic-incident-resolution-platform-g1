# Operations runbook

How to run, check and repair the deployed platform. Every command here was run against the
shared deployment during the [2026-10-02 audit](audit_2026-10-02.md) unless it says
otherwise. For the demo script, see [demo_runbook.md](demo_runbook.md).

**Never put a credential in this file, a PR, an issue or a chat.** Where a secret is named
below, it is a *name*; the value lives in the deployment's `.env` and in GitHub environment
secrets.

## 1. What runs where

| Piece | Where | Notes |
|---|---|---|
| FastAPI (`barq-api`) | shared EC2, port 8000, plain HTTP | **No TLS.** A page served over HTTPS (for example inside ServiceNow) cannot call it from the browser (mixed content). |
| Celery worker + beat (`barq-celery-worker`) | same host | One container: `--beat` is embedded, so there must be exactly **one** worker container. |
| PostgreSQL, Redis, Qdrant | same host, Docker network | Not reachable from the internet (verified 2026-10-02: 5432, 6379, 6333, 6334 closed). |
| ServiceNow | shared instance (`dev407364`) | The Business Rule posts events to the API. |
| Traces | Langfuse | Needs a login; send screenshots, not links. |

Only SSH (key-only) and 8000 are open on the host. The deploy account is `ubuntu`; the
application accounts are separate and have no shell.

## 2. Is it healthy?

```bash
curl -s http://<host>:8000/health      # {"status":"ok"}: the process is up
curl -s http://<host>:8000/ready       # database + redis connected
```

`/ready` checks PostgreSQL and Redis **only**. It does not check Qdrant, the model proxy or
ServiceNow, so a green `/ready` does not prove that retrieval, drafting or write-back work.
A real check is an eligible incident reaching `complete`.

On the host:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'                       # all five "healthy"
docker exec barq-celery-worker celery -A app.workers.celery_app inspect ping
cd ~/barq-sprints-agentic-incident-resolution-platform-g1 && git rev-parse --short HEAD   # deployed commit
```

## 3. Deploying and rolling back

Every merge to `main` runs `.github/workflows/deploy.yml`: it re-runs lint, types and the
unit tests, then SSHes to the host, checks out the exact commit, **builds the image on the
host**, runs `alembic upgrade head`, restarts `api` and `celery-worker`, and verifies
`/ready` and a worker ping. If any step fails it restores the previous commit's code.

Know what a rollback does **not** undo: migrations are forward-only, so a rolled-back release
can be running against a newer schema. Write migrations to be backward compatible with the
previous release for one deploy.

The deploy refuses to run if the checkout has uncommitted *tracked* changes. An untracked
file is ignored by that check and silently stays on the server (one was found there on
2026-10-02); do not leave scripts on the host.

## 4. Disk and memory

The host has about 19 GB of disk and 3.8 GB of RAM with no swap, and the images are built on
it, so old images and build cache accumulate on every deploy. On 2026-10-02 the disk was at
80% with about 17 GB of Docker data reclaimable.

```bash
df -h /
docker system df
docker builder prune -f --filter "until=72h"      # unused build cache older than 3 days
docker image prune -f                              # dangling images
```

These never touch running containers or named volumes. Do **not** run `docker system prune
--volumes`: that would delete the PostgreSQL, Redis and Qdrant data.

The worker alone uses about 1.2 GB (it holds the embedding and reranker models). With no
swap, a burst of concurrent incidents can exhaust memory; lower `WORKER_CONCURRENCY` before
raising load.

## 5. Runs stuck in `awaiting_approval`

A run is parked when the graph raised a human-approval interrupt. It stays parked until
someone decides it; nothing expires it.

List them (read-only):

```bash
docker exec barq-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  select left(e.execution_id::text,8) id, ev.incident_number, e.started_at::timestamp(0)
  from executions e join events ev on ev.id = e.event_record_id
  where e.status = '\''awaiting_approval'\'' order by e.started_at"'
```

Decide one through the operator API or the `/review?execution_id=<id>` page. You need an
operator token: exchange the operator client credentials at `POST /api/v1/oauth/token`
(`grant_type=client_credentials`, HTTP Basic with `OPERATOR_CLIENT_ID` and the
`WEBHOOK_AUTH_TOKEN` value). Then:

```bash
curl -s -X POST http://<host>:8000/api/v1/approvals/<execution_id>/decide \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"decision":"rejected","reason":"stale: superseded"}'
```

Meaning of the answers: `200` applied and recorded; `409` already decided, not paused, or
**no stored interrupt** (nothing is recorded in that case, since an approval for a run no one
was waiting on can never be corrected); `404` unknown id. The decision row is immutable.

`reason` is limited to 4,000 characters and `evidence` to 16 KiB serialised.

On 2026-10-02 there were 29 such runs, all from 2026-09-29 and all with a stored interrupt, so
each can be decided this way. ServiceNow incidents flagged `awaiting_approval` without a
matching parked run (processed before the approval fix) have no run to decide; resolve those
in ServiceNow.

## 6. Dead-letter queue

```bash
just dlq-list                  # or GET /api/v1/dlq
just dlq-replay <event_id>     # or POST /api/v1/dlq/{event_id}/replay
```

A replay carries the original trace id and removes the old record only after the new message
is enqueued. An empty list from the API means the queue is empty; a Redis failure returns 503,
not an empty list.

**Events from another instance end up here.** If a second ServiceNow instance (for example a
personal developer instance) posts to this backend, its events carry `sys_id`s that do not
exist on the shared instance and fail with `ServiceNowNotFoundError` after one attempt. Nineteen
such failures were recorded on 2026-10-01. Point developer instances at their own backend.

## 7. Switching things off

| To stop | Set, then restart `api` and `celery-worker` |
|---|---|
| Semantic caching (every incident runs the full graph) | `ENABLE_SEMANTIC_CACHE=false` |
| Writing to ServiceNow (dry run) | `AGENT_WRITE_BACK_ENABLED=false` |
| Residual-PII detector (default) | `AGENT_PII_DETECTION_MODE=disabled` |

`GET /api/v1/config` reports the live values. Its `active_feature_flags` map is mostly
informational; only `eval_benchmarks` and `semantic_caching` (which mirrors
`ENABLE_SEMANTIC_CACHE`) change behaviour.

## 8. Reading a blocked or skipped run

`executions.termination_cause` and the `act` node's work note say why a run did not draft:

| Cause | Meaning |
|---|---|
| `skipped_ineligible` | Locked by an analyst, already processed, AI not enabled, unsupported category or inactive. No model was called. |
| `escalated_blocked` | The input guardrail (pattern hit confirmed, or classifier flagged or unavailable), `verify_evidence` or `safety_check` stopped it. |
| `escalated_high_risk` | Priority 1 or a security classification: parked for approval before any search. |
| `escalated_no_evidence` / `escalated_low_confidence` | Nothing grounded enough was found; parked for a human answer. |
| `suggested` | A cited draft was written. |

A semantic-cache follower in an already-resolved cluster is resolved with no LLM calls **only
if** it is eligible, its text passes pattern screening and its own risk is low; otherwise it
runs the full graph. Its trace then holds only a `cluster_cache.resolve_follower` span.

## 9. Traces

Read a run from the API rather than the UI. The legacy `/api/public/traces/{id}` returns 410
on this organisation; use `GET /api/public/v2/observations` with `traceId=…` and request the
field groups you need (`fields=core,basic,time,io,metadata,model,usage`). **Without the
`model` and `usage` groups the response omits model, tokens and cost, which looks like
missing data and is not.**

## 10. Red-team the guardrails

```bash
uv run python scripts/run_redteam.py            # prints the summary, exit 1 if any case moved
uv run python scripts/run_redteam.py --write    # refresh docs/evidence/redteam_results.json
```

This exercises pattern screening and redaction only (no model call). See
[sprint-3/guardrail_design.md](sprint-3/guardrail_design.md#red-team-corpus-and-recorded-results--dataadversarialredteam_corpusjson).

## 11. Known gaps

- No TLS on the API; the approval screen is plain HTTP.
- `/ready` does not check Qdrant, the model proxy or ServiceNow.
- No metrics endpoint, no rate limiting and no notifications when an approval is needed.
- Nothing expires an unanswered approval.
- No CI evaluation gate (PRD FR-20) and no CI image build; images are built on the host.
- The shared Qdrant collection (50 points, 16 article versions on 2026-10-02) does not
  contain the manual-derived articles that are published in ServiceNow's knowledge base
  (84 published articles); the ingestion pipeline exists but has not been run against it.
