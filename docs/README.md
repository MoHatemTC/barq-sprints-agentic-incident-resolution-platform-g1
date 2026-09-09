# Documentation index

See [ROADMAP.md](ROADMAP.md) for the full four-sprint PRD scope, and
[../TEAM.md](../TEAM.md) for who owns each task.

## Sprint 1 — Platform Build

Covers FR-01, FR-02 and FR-06.

**Definition of done — the target state, not a description of today.** The application
exports cleanly as an update set; an execution log record can be written through the API
by the integration user; and no admin credential exists anywhere in the repository or
configuration. The third clause is tracked in issue #7 and the second in #8; neither is
met yet.

### S1.1 — Scoped application and Incident field model

| Document | What it is for |
|---|---|
| [sprint1_field_model.md](sprint1_field_model.md) | **The contract every other workstream writes against.** Each field's type, permitted values, writing component, intended write permission, and the architectural reason suggestion and resolution stay separate |
| [sprint1_implementation_runbook.md](sprint1_implementation_runbook.md) | How to reproduce the application from the SDK source |
| [sprint1_acceptance_matrix.md](sprint1_acceptance_matrix.md) | Each acceptance requirement mapped to its implementation and evidence |
| [sprint1_source_pdi_verification.md](sprint1_source_pdi_verification.md) | What was verified on the source instance |
| [sprint1_secondary_import_verification.md](sprint1_secondary_import_verification.md) | Clean preview and commit on a second instance |
| [sprint1_submission_checklist.md](sprint1_submission_checklist.md) | Pre-submission checks |
| [screenshots/s1-1/](screenshots/s1-1/) | Incident form, choice list, validation, and import evidence |

### S1.3 — Eligibility and outbound event

| Document | Status |
|---|---|
| [sprint1_event_contract_draft.md](sprint1_event_contract_draft.md) | **Draft scaffold, not a deliverable.** Six eligibility conditions and the identifier-only payload, unverified on a PDI |

## Conventions

- Scope prefix for every Incident column: `x_2215032_ai_inc_0_ai_`
- Scripts use **internal** choice values (`in_progress`), never display labels
- No artifact may live in the ServiceNow Global scope
- The outbound event carries `event_id`, `sys_id`, `number` and `event_type` only — never the
  full incident record. The backend retrieves what it is authorised to retrieve, so ACLs
  remain the single source of truth and the contract survives new Incident fields
