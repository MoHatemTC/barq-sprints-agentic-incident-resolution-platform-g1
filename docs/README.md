# Documentation index

Documentation lives in one folder per task, inside one folder per sprint —
`docs/sprint-N/sN.M-short-name/`. Each task folder has its own `README.md`
naming the owner, the tracking issues, and what belongs there. See
[ROADMAP.md](ROADMAP.md) for the full four-sprint PRD scope, and
[../TEAM.md](../TEAM.md) for who owns what.

## Sprint 1 — Platform Build

[docs/sprint-1/](sprint-1/) — status table and links for all five tasks.

| Task | Owner |
|---|---|
| [S1.1 — Scoped app and field model](sprint-1/s1.1-scoped-app-and-field-model/) | [@ali-ezz](https://github.com/ali-ezz) |
| [S1.2 — Execution log and OAuth identity](sprint-1/s1.2-execution-log-and-oauth-identity/) | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) |
| [S1.3 — Eligibility rule and event](sprint-1/s1.3-eligibility-rule-and-event/) | [@ahmedtamer101](https://github.com/ahmedtamer101) |
| [S1.4 — Knowledge and Qdrant](sprint-1/s1.4-knowledge-and-qdrant/) | [@kerolos-mohsen](https://github.com/kerolos-mohsen) |
| [S1.5 — ServiceNow client](sprint-1/s1.5-servicenow-client/) | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |

The S1.1 folder is the one with real content today — field dictionary,
implementation runbook, acceptance matrix, PDI verification records,
submission checklist and screenshots. The other four hold a `README.md`
recording status and ownership until their pull requests land.

## Conventions

- Scope prefix for every Incident column: `x_2215032_ai_inc_0_ai_`
- Scripts use **internal** choice values (`in_progress`), never display labels
- No artifact may live in the ServiceNow Global scope
- The outbound event carries `event_id`, `sys_id`, `number` and `event_type` only —
  never the full incident record. The backend retrieves what it is authorised to
  retrieve, so ACLs remain the single source of truth and the contract survives
  new Incident fields
- New sprint documentation follows the same `docs/sprint-N/sN.M-name/` layout,
  with a `README.md` naming the owner from the day the task is created — not
  added after the fact
