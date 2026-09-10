# Sprint 1 — Platform Build

Goal: the platform side exists as a real ServiceNow application, with an audit
trail and an identity a risk owner would sign off. Covers FR-01, FR-02, FR-06.

Tracked in the [Sprint 1 milestone](../../milestone/1).

| Task | Scope | Owner | Status |
|---|---|---|---|
| [S1.1](s1.1-scoped-app-and-field-model/) | Scoped application and Incident field model | [@ali-ezz](https://github.com/ali-ezz) | ✅ Merged |
| [S1.2](s1.2-execution-log-and-oauth-identity/) | AI Execution Log table, OAuth integration identity, field-level ACLs | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | 🟡 In review — [#14](../../pull/14) |
| [S1.3](s1.3-eligibility-rule-and-event/) | Eligibility Business Rule, identifier-only outbound event | [@ahmedtamer101](https://github.com/ahmedtamer101) | 🔴 Not started — draft in [#18](../../pull/18) |
| [S1.4](s1.4-knowledge-and-qdrant/) | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | 🔴 Not started |
| [S1.5](s1.5-servicenow-client/) | ServiceNow Table API client, incident write-back | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | 🟡 In progress — [#5](../../pull/5) |

**Definition of done — the target state, not a claim about today.** The
application exports cleanly as an update set; an execution log record can be
written through the API by the integration user; no admin credential exists
anywhere in the repository or configuration.

This status table is a snapshot from when it was last edited. The issues and
pull requests linked above are the live source of truth.
