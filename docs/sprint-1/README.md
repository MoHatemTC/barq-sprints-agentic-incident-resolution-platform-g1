# Sprint 1 — Platform Build

Goal: the platform side exists as a real ServiceNow application, with an audit
trail and an identity a risk owner would sign off. Covers FR-01, FR-02, FR-06.

Tracked in the [Sprint 1 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/1).

| Task | Scope | Owner | Status |
|---|---|---|---|
| [S1.1](s1.1-scoped-app-and-field-model/) | Scoped application and Incident field model | [@ali-ezz](https://github.com/ali-ezz) | 🟡 Merged with open items — [#1](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/1), SDK reproducibility [#63](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/63); tracking [#56](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/56), [#69](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/69), [#73](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/73) |
| [S1.2](s1.2-execution-log-and-oauth-identity/) | AI Execution Log table, OAuth integration identity, field-level ACLs | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | 🟡 Merged with open items — [#29](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/29), rework [#32](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/32); tracking [#46](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/46)–[#51](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/51), [#68](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/68), [#102](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/102) |
| [S1.3](s1.3-eligibility-rule-and-event/) | Eligibility Business Rule, identifier-only outbound event | [@ahmedtamer101](https://github.com/ahmedtamer101) | 🟡 Merged with open items — [#30](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/30); tracking [#93](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/93), [#94](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/94), [#99](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/99), [#100](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/100), [#103](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/103) |
| [S1.4](s1.4-knowledge-and-qdrant/) | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | 🟡 Merged with open items — [#28](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/28), [#33](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/33), KB publishing [#88](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/88) (which superseded the closed [#39](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/39)); tracking [#89](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/89), [#90](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/90), [#91](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/91) |
| [S1.5](s1.5-servicenow-client/) | ServiceNow Table API client, incident write-back | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | 🟡 Merged with open items — [#27](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/27); tracking [#42](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/42), [#43](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/43), [#64](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/64), [#66](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/66), [#67](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/67), [#74](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/74) |

🟡 means the work is merged or in review and its tracking issues still have open
items. ✅ is reserved for a task with no open follow-ups, which is why no row carries
it today. The symbol describes the issue list, not the quality of the merge.

**Definition of done — the target state, not a claim about today.** The
application exports cleanly as an update set; an execution log record can be
written through the API by the integration user; no admin credential exists
anywhere in the repository or configuration.

Sprint 1 closed on 13 September 2026. Every task is merged to `main`; what remains
open is follow-up work, tracked per row above and carried into Sprint 2.

This status table is a snapshot, last updated 16 September 2026. The
[pull requests](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pulls) and [issues](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues) are the live source of truth.

Links here are absolute. Relative forms like `../../pull/14` resolve against
`/blob/main/` from this directory and 404; that pattern only works from the
repository root.
