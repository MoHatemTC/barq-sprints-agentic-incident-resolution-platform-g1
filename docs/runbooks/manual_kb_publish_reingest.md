# Runbook — manual KB publish and re-ingest (production)

Purpose: the controlled procedure for moving a validated release candidate
into the production ServiceNow KB and Qdrant collection. This is an
**explicitly authorized operator action** — merging the integration code is
not authorization, and this runbook is not executed until the Stage A gate
has passed with an agreed metric policy.

## Preconditions (all mandatory)

- [ ] Release fingerprint recorded and current (see the local-validation runbook).
- [ ] Stage A gate passed: 87/87 rows evaluated by the real judge, zero
      integrity violations, metric floors agreed and signed in
      `eval/manual_stage_a_policy.json`.
- [ ] Manifest review signed: `docs/manual_kb_manifest_review.md` records the
      approver and the reviewed manifest hash; the draft hash matches
      `data/corpus/manual_kb_manifest.json`.
- [ ] Publish dry-run report clean (64 publishable units, 11 documented skips,
      zero failed).
- [ ] Maintenance window agreed: no deploy, no re-seed, no KB edits, demos
      paused. New graph executions paused and drained per the operator
      procedure; knowledge-capture approvals are inside the scope.

## 1. Preliminary inventory (reads only)

Record what exists before touching anything — ServiceNow KB rows (paginated,
all states) and the Qdrant collection's article identities, including
human-captured KB1xxx articles. The collection contains live knowledge that
the committed corpus does not know about; **the corpus is not the inventory**.

## 2. Final inventory and backup (after pause-and-drain)

Anything created between step 1 and the pause lands here.

- Qdrant: snapshot the collection.
- ServiceNow: full paginated export of the KB (sys_id, number, version,
  state, content, metadata). A prefix-ID list alone cannot rebuild content —
  the export is the restore source.

Do not proceed if either backup is incomplete. Human-captured articles
missing from the backup block the release.

## 3. Publish

```bash
uv run python scripts/publish_manual_sections.py \
  --report /tmp/manual_publish_report.json --allow-writes
```

64 units go out through `publish_article` with the `kb_publisher` identity:
idempotent by `u_source_id`, draft-then-state, read-back verified — the stored
body must now match the sent content in full, not just carry the Source
marker. Aliases (KB0001–KB0010) and the retired archival unit are skipped by
design; the retired KB0010-v1.0 stays retired and filter-excluded.

On partial failure: fix nothing by hand mid-run. Re-run the same command —
it looks up the same versioned source IDs, creates only missing rows, reports
`unchanged`/`updated` for the rest, and appends failures to the report.

## 4. Re-seed

```bash
uv run python scripts/seed_qdrant.py \
  --url "$PROD_QDRANT_URL" \
  --collection "$PROD_COLLECTION" \
  --with-manual-kb
```

Replace-per-article, bounded to the supplied corpus: human captures and any
unrelated records survive. Verification is by expected point ID and chunk
content — a failure exits non-zero; do not force-recreate.

## 5. Verify and resume

- Point-level: spot-check KB2001-v1.0 … KB2065-v1.0 payloads carry
  `content_purpose` and (where applicable) warnings; KB0001-v2.0 carries
  `source_sections: ["6.4"]`.
- Security: restricted units (7.3, 7.5, 9.2, 9.5, 9.6, KB2065) are present but
  only reachable within their tier; retired KB0010-v1.0 and the archived scan
  KB2022 are filter-invisible.
- Retrieval probes: a handful of real queries through the agent path; confirm
  human-capture evidence still resolves (KB1002).
- Reconciliation: the dry-run plan (`build_reconciliation_plan`) lists raw
  legacy section points and superseded stressor articles for removal **only
  after** their replacements are verified — apply is a separate, explicit,
  inventory-bound step, never a side effect of seeding.

Resume processing only when the data is consistent. Archive the publish
report, the seed output and the verification evidence with the release
fingerprint.

## Known limitations (state them in the handover)

- Publisher read-back proves what the publisher identity wrote, not that an
  ordinary user can see or search the articles — verify visibility manually in
  the UI once.
- Delete/upsert is not transactional across Qdrant and ServiceNow; the backup
  in step 2 is the recovery path (see the recovery runbook).
- Concurrent bulk publishing is not supported; serialize the run.
