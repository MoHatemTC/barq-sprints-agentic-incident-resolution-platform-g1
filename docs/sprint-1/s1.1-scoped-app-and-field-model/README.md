# S1.1 — Scoped Application and Incident Field Model

Owner: [@ali-ezz](https://github.com/ali-ezz)  
Status: Implemented, clean-import verified, and merged through [PR #1](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/1)

This folder is the final evidence package for BARQ G1 Sprint 1 task S1.1. The ServiceNow application is named **AI Incident Orchestrator**, uses scope `x_2215032_ai_inc_0`, and extends Incident with 13 scoped fields covering eligibility, lifecycle, classification, confidence, suggestion, resolution, attribution, timing, human review, human lock, and failure diagnostics.

## Required deliverables

- [Field dictionary](field-model.md) — all 13 fields, types, values, exact writing components, persistence components, intended writer/read roles, lifecycle semantics, and the suggestion/resolution rationale.
- [Exported update-set XML](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml) — 39 update records and zero delete actions.
- [Screenshot evidence](screenshots/) — scoped application, current update set, complete Default-view form section, processing-state choices, confidence validation, clean secondary preview/commit, and post-import verification.

## Verification records

- [Acceptance evidence matrix](acceptance-matrix.md)
- [Source-PDI verification](source-pdi-verification.md)
- [Secondary-PDI clean-import verification](secondary-import-verification.md)
- [Implementation runbook](implementation-runbook.md)
- [Submission checklist](submission-checklist.md)

The final XML was previewed and committed on authorized teammate PDI `dev204871`, on the same Australia release as source PDI `dev434590`. The secondary preview reported 39 inserts, zero updates, zero deletions, and zero collisions. Commit and post-import form/validation testing completed without manual repair.

## Scope boundary

S1.1 defines the application and field-model contract. S1.2 owns OAuth identity and ACL implementation; S1.3 owns runtime eligibility and Human Lock enforcement. Those downstream responsibilities are documented here but are not falsely claimed as S1.1 implementation.
