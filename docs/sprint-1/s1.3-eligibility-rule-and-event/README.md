# S1.3 — Eligibility Business Rule, identifier-only outbound event

**Owner:** [@ahmedtamer101](https://github.com/ahmedtamer101) · **Tracking:** [#10](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/10)

## Status

S1.3 eligibility and asynchronous event delivery are implemented and have been
verified on the shared PDI. The canonical documents below record the event
contract, verification evidence, and current Sprint 1 transport status.

PR #30 includes the eligibility Business Rule, relevant-update filtering,
asynchronous event queue, Script Action and RESTMessageV2 transport, retry
handling, and escalation to human review.

Sprint 1 proves outbound delivery against a request-inspection endpoint. Outbound
OAuth remains pending mentor confirmation and is still tracked as an unresolved
acceptance item in #10.

## Canonical deliverables

- [Outbound Event Contract v1](event-contract-v1.md)
- [S1.3 Eligibility and Outbound Event Verification](verification-evidence.md)

## Requirements

FR-03 (evaluate eligibility on insert and relevant update; emit nothing when
inactive, unsupported, already processed, already running, or human-locked)
and FR-04 (the event carries event ID, sys_id, number and event type only).

## Deploying S1.3 — read before installing

**S1.3 ships as the update set** (`servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_3.xml`),
not as a Fluent deploy. The two are not interchangeable: the exported records and
the sys_ids in `sdk-app/src/fluent/generated/keys.ts` do not correspond, so the same
eligibility and retry rules are defined twice under different identifiers.

Running `now-sdk deploy` against an instance that already holds this update set
creates a **second** eligibility Business Rule and a **second** retry rule. Both
would then fire on the same save, emitting **two outbound events per eligible
incident**.

Install one way or the other, never both. Reconciling the two definitions onto a
single set of sys_ids is tracked as follow-up work.
