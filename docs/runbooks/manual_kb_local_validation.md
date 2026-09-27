# Runbook — manual KB local validation

Purpose: reproduce the full manual-KB integration on a scratch Qdrant from a
clean checkout, and produce the evidence a release candidate needs. Scratch
only — nothing here touches production ServiceNow or the production Qdrant
collection.

## 0. Release fingerprint (record first, verify always)

A release candidate is only meaningful against one exact set of inputs. Record
all of these in the release notes; if any changed, previous evaluation results
are void and the gates rerun.

```bash
git rev-parse HEAD
sha256sum data/barq-system-kb.pdf \
          data/corpus/manual_kb_manifest.json \
          data/corpus/manual_sections.json \
          data/corpus/manual_source.json \
          data/structured-io/barq_rag_eval_dataset.json \
          eval/manual_stage_a_policy.json
uv run python scripts/manual/validate_manual.py --check   # PDF hash + coverage drift
```

Expected PDF hash: `c535243f00547211c2a7076bc5dc91c80d19a4960b94dc7932bbca546db7d9d4`.
The manifest is valid only while its per-section content hashes still match the
committed extraction — the adapter re-preflights this on every call.

## 1. Environment

- Python deps: `uv sync` (evaluation extras: `uv sync --group eval`).
- A local Qdrant (Docker: `docker run -p 6333:6333 qdrant/qdrant`). Note the
  URL; every command below takes it explicitly and never defaults to
  production.

## 2. Seed a scratch collection

```bash
uv run python scripts/seed_qdrant.py \
  --url http://localhost:6333 \
  --collection scratch_validation \
  --with-manual-kb --no-stressors
```

Expected: 76 article identities (11 baseline incl. the retired KB0010-v1.0,
plus 65 manifest units — the 10 Section-6 aliases collapse onto the baseline
identities). Seeding is idempotent and replace-per-article; re-running must
change nothing. Raw manual sections are never seeded here — `--manual-corpus`
is a separate opt-in scratch path that must target a *different* collection.

## 3. Gates (all green, in this order)

```bash
just check                                   # lint, format, types, unit tests
uv run pytest tests/retrieval/manual/ -q     # manifest, adapter, ingestion contracts
uv run pytest tests/eval/ -q                 # dataset contract + Stage A runner
uv run pytest tests/publishing/ -q           # publisher + body verification
```

## 4. Stage A evaluation

```bash
just eval-stage-a http://localhost:6333 scratch_validation
```

Report-only until `eval/manual_stage_a_policy.json` is agreed and signed
(`thresholds_agreed: true` with non-null floors). The report (`eval/stage_a_report.json`)
must account for all 87 answerable rows (13 conversation turns stay pending
Stage B) and show **zero integrity violations** — any `must_not_retrieve` hit
is a stop, not a metric.

## 5. Publish dry-run

```bash
uv run python scripts/publish_manual_sections.py \
  --report /tmp/publish_dry_run.json
```

Default is read-only. Expect 64 publishable units, 11 skips (10 Section-6
aliases already published as KB0001–KB0010, 1 retired archival unit). The
report pins the manifest hash. A live run additionally needs the
`SERVICENOW_*` credentials and `--allow-writes` — see the publish/re-ingest
runbook.
