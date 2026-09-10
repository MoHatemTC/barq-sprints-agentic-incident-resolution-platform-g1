# S1.3 — Eligibility Business Rule, identifier-only outbound event

**Owner:** [@ahmedtamer101](https://github.com/ahmedtamer101) · **Tracking:** [#10](../../../../issues/10)

## Status

Not started by the owner. A draft scaffold exists in
[PR #18](../../../../pull/18) — six eligibility conditions and the
identifier-only payload — but it is unverified on a PDI and explicitly a
starting point, not a deliverable.

## What lands in this folder once merged

- `eligibility-and-event-contract.md` — the six conditions mapped to field
  names, the payload shape, and the rationale for sending identifiers only
- The Business Rule script, verified against a request-inspection endpoint
  since the receiving webhook is Sprint 2

## Requirements

FR-03 (evaluate eligibility on insert and relevant update; emit nothing when
inactive, unsupported, already processed, already running, or human-locked)
and FR-04 (the event carries event ID, sys_id, number and event type only).
