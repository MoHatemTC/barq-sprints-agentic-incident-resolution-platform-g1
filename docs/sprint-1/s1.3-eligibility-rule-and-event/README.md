# S1.3 — Eligibility Business Rule, identifier-only outbound event

**Owner:** [@ahmedtamer101](https://github.com/ahmedtamer101) · **Tracking:** [#10](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/10)

## Status

In review in [#30](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/30): the eligibility Business Rule with all six
conditions and relevant-update filtering, an asynchronous event queue, a Script
Action and RESTMessageV2 transport, and retry with escalation to human review.

Sprint 1 proves emission against a request-inspection endpoint, since the receiving
webhook is Sprint 2. Outbound OAuth is therefore not exercised in Sprint 1.

[PR #18](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/18) remains open as an unverified reference scaffold at the owner's
request; it is not the deliverable.

## What lands in this folder once merged

- `eligibility-and-event-contract.md` — the six conditions mapped to field
  names, the payload shape, and the rationale for sending identifiers only
- The Business Rule script, verified against a request-inspection endpoint
  since the receiving webhook is Sprint 2

## Requirements

FR-03 (evaluate eligibility on insert and relevant update; emit nothing when
inactive, unsupported, already processed, already running, or human-locked)
and FR-04 (the event carries event ID, sys_id, number and event type only).
