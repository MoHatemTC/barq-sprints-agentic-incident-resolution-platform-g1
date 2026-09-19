# S1.2 — AI Execution Log table, OAuth integration identity, field-level ACLs

**Owner:** [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) · **Tracking:** [#7](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/7), [#8](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/8)

## Status

Merged in [#29](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/29) and [#32](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/32): the execution log table, a dedicated non-admin
service account, field-level ACLs and an automated verification harness.

[#123](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/123) re-exports the update set so that it installs on a clean instance with
nothing in the Global scope, and adds a security fixes update set. The safety stop is a
scoped ACL condition, journal read is narrowed, and access tracking is Enforcing.
The harness passes 28 of 28 on a clean PDI. Import order and the one-off cleanup for
instances that hold the earlier set are in [audit-and-identity.md](audit-and-identity.md) §8.

## What lands in this folder once merged

- `audit-and-identity.md` — the audit trail design, least-privilege rationale, and OAuth token lifecycle
- The verified permission matrix — observed results from executed attempts, not an intended design
- Screenshots or exports evidencing the scoped rebuild

## Requirements

FR-02 (an AI Execution Log record for every processing attempt, including failures)
and FR-06 (OAuth as a least-privilege integration user, no admin credential in
configuration or code).
