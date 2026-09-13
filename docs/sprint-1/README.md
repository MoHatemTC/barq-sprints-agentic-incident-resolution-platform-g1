# Sprint 1 — Platform Build

Goal: the platform side exists as a real ServiceNow application, with an audit
trail and an identity a risk owner would sign off. Covers FR-01, FR-02, FR-06.

Tracked in the [Sprint 1 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/1).

| Task | Scope | Owner | Status |
|---|---|---|---|
| [S1.1](s1.1-scoped-app-and-field-model/) | Scoped application and Incident field model | [@ali-ezz](https://github.com/ali-ezz) | ✅ Merged — [#1](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/1) |
| [S1.2](s1.2-execution-log-and-oauth-identity/) | AI Execution Log table, OAuth integration identity, field-level ACLs | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | 🟡 Merged with open items — [#29](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/29); rework in review [#32](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/32); tracking [#7](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/7), [#8](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/8) |
| [S1.3](s1.3-eligibility-rule-and-event/) | Eligibility Business Rule, identifier-only outbound event | [@ahmedtamer101](https://github.com/ahmedtamer101) | 🟡 In review — [#30](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/30) |
| [S1.4](s1.4-knowledge-and-qdrant/) | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | 🟡 Merged with open items — [#28](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/28), [#33](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/33); KB publishing in review [#39](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/39); tracking [#11](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/11) |
| [S1.5](s1.5-servicenow-client/) | ServiceNow Table API client, incident write-back | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | ✅ Merged — [#27](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/27) |

**Definition of done — the target state, not a claim about today.** The
application exports cleanly as an update set; an execution log record can be
written through the API by the integration user; no admin credential exists
anywhere in the repository or configuration.

This status table is a snapshot, last updated 13 September 2026. The
[pull requests](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pulls) and [issues](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues) are the live source of truth.

Links here are absolute. Relative forms like `../../pull/14` resolve against
`/blob/main/` from this directory and 404; that pattern only works from the
repository root.
