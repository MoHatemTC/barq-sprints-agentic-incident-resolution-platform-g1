# S1.3 — Eligibility Business Rule, identifier-only outbound event

**Owner:** [@ahmedtamer101](https://github.com/ahmedtamer101) · **Tracking:** [#10](../../../../issues/10)

## Status

Not started by the owner. [`eligibility-and-event-contract.md`](eligibility-and-event-contract.md)
and the Business Rule script are a **draft scaffold** from
[PR #18](../../../../pull/18) — six eligibility conditions and the
identifier-only payload — unverified on a PDI and explicitly a starting
point, not a deliverable. @ahmedtamer101 owns changing, replacing, or
verifying it.

## What's here

- [`eligibility-and-event-contract.md`](eligibility-and-event-contract.md) —
  the six conditions mapped to field names, the payload shape, and the
  rationale for sending identifiers only
- `../../../servicenow/ai_incident_orchestrator/business_rules/s1_3_eligibility_event.js` —
  the draft Business Rule script

Still needed: verification against a request-inspection endpoint, since the
receiving webhook is Sprint 2.

## Requirements

FR-03 (evaluate eligibility on insert and relevant update; emit nothing when
inactive, unsupported, already processed, already running, or human-locked)
and FR-04 (the event carries event ID, sys_id, number and event type only).
