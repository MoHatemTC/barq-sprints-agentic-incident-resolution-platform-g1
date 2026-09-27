# Runbook — manual KB recovery and retry

Purpose: what to do when a release step fails halfway. The rule that governs
everything here: **never discard live knowledge to restore a count.** A new
human capture made during the window is reconciled forward, not rolled back.

## Failure before any mutation

Keep the reports. Fix locally, rebuild the scratch candidate, rerun the
affected gates (see the local-validation runbook). Nothing in production
changed; no backup restore is involved.

## Partial ServiceNow publication

1. Read the publish report: `results` (with per-article `sys_id` and stored
   body hashes) are the journal of what succeeded.
2. Re-run the same publish command with the same manifest — it re-looks-up the
   same versioned source IDs, creates only the missing rows, and reports
   `unchanged`/`updated` for existing ones. It never allocates new numbers and
   never creates a duplicate.
3. A row that refused a PATCH (ACL) surfaces as
   `ServiceNowWriteRejectedError` naming the sys_id: published/retired rows
   are immutable to the publisher identity; changed content needs a version
   bump through a reviewed manifest change, or a KB-owner action. Record the
   refusal; do not retry it blindly.
4. Reconcile target states through the client, not the UI: read back and
   compare against the report's stored-body hashes.

## Seed / delete / upsert failure

Keep processing paused — do not "just re-seed" over a half-written state.

- Preferred: finish the same validated release idempotently (same command,
  same collection). Replace-per-article and verified point IDs make this safe.
- If the collection is inconsistent beyond that: restore the pre-release
  Qdrant **snapshot** (step 2 of the publish runbook) and verify the inventory
  matches — article identities and counts, human captures present, restricted
  tiers intact. Restoring application code alone is **not** recovery; the
  snapshot is the state.

## ServiceNow/Qdrant divergence

The two systems are reconciled separately:

- Qdrant restoration does not undo published ServiceNow rows. Use the publish
  journal to know which rows are new; they stay published unless the KB owner
  retires them through the same client.
- Version retirement/reversion may require KB-owner rights if ACLs reject the
  publisher; surface ACL refusals, never swallow them.

## If writes happened despite the maintenance window

Capture and reconcile them before any snapshot restore. A human capture that
arrived between the preliminary inventory and the pause must appear in the
final backup (or be re-verified after restore) — never discard new human
knowledge to return to a previous count.

## Evidence to keep

Every recovery action is recorded with: the release fingerprint, the failing
step's report, the restore snapshot identity, and the post-restore inventory
verification. Keep backups and reports through the agreed observation window;
deletion is never part of a first-release success path.
