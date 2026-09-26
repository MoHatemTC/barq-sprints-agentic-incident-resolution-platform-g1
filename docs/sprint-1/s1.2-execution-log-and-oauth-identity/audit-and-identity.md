# Sprint 1 (Task S1.2): ServiceNow Security & Audit Foundation
## Identity, Least-Privilege Access Control, Append-Only Execution Log & Verification

---

### Document Status & Metadata

- **Work Item**: Sprint 1 — Task S1.2: AI Execution Log Table, OAuth Integration Identity & Least-Privilege ACLs
- **Application Scope**: `AI Incident Orchestrator` (`x_2215032_ai_inc_0`)
- **Application Scope ID**: `51a63bbf738bc7502aedfed25ab8b789`
- **Update Set**: `AI Incident Orchestrator - S1.2 Execution Log and ACLs`
- **Service Account**: `ai_orchestrator_svc` (Machine identity, non-admin, Web Service Access Only)
- **Primary Integration Role**: `x_2215032_ai_inc_0.integration_writer`
- **Target Instance**: `dev407364.service-now.com`
- **Verification Harness**: [`scripts/verify_permissions.py`](../../../scripts/verify_permissions.py)
- **Machine-Readable Report**: [`scripts/verification_report.json`](../../../scripts/verification_report.json)
- **Verification Status**: **28 / 28 PASS** on a clean install (`dev434590`, 2026-09-16)

---

### 1. Executive Summary & Architectural Overview

Task S1.2 delivers the foundational security, identity, and audit infrastructure for the AI Incident Resolution Platform. Built under strict zero-trust principles and ServiceNow private-scope isolation, the deliverable unites three core architectural pillars:

1. **Dedicated Non-Admin Service Identity (FR-06)**: An automated machine account (`ai_orchestrator_svc`) authenticating exclusively via OAuth 2.0. The account holds no administrative, security admin, or ITIL fulfiller roles, eliminating privilege creep.
2. **Append-Only AI Execution Log Table (FR-02)**: A scoped audit repository (`x_2215032_ai_inc_0_ai_execution_log`) that records every processing attempt (`started`, `succeeded`, `failed`, `blocked`, `awaiting_approval`, or `abandoned`) with indexed execution IDs for tamper-proof trace correlation. Deletion is strictly prohibited via RBAC ACLs (non-admin roles denied delete access).
3. **Least-Privilege Field-Level ACL Boundaries**: Explicit Access Control Lists that grant the integration account write access strictly to internal diagnostic work notes and agent-authored scoped AI fields (including `ai_classification`, `ai_human_review_required`, and downstream orchestration state fields), while enforcing hard, unbypassable denials on incident lifecycle state, assignments, customer comments, priority, and human-lock safety controls.

---

### 2. Service Identity & OAuth 2.0 Architecture

#### Service Account Specification
- **User ID (`user_name`)**: `ai_orchestrator_svc`
- **Display Name**: `AI Orchestrator Service Account`
- **Account Type**: Machine Identity
- **Web Service Access Only**: `true` (Interactive UI login disabled)
- **Active**: `true`
- **Admin Privileges**: `false` (Lacks `admin`, `security_admin`, and `itil`)

#### Assigned Roles & Minimum Privilege Rationale

| Role Name | Scope | Purpose & Need |
|---|---|---|
| `x_2215032_ai_inc_0.integration_writer` | Scoped (`x_2215032_ai_inc_0`) | Primary integration role. Grants write permission to all 12 permitted scoped incident AI fields and internal `work_notes`. **Automatically includes** `ai_execution_log_user` and `snc_platform_rest_api_access` as contained sub-roles (via `sys_user_role_contains`). |
| `x_2215032_ai_inc_0.ai_execution_log_user` | Scoped (`x_2215032_ai_inc_0`) | Grants create and read access to the AI Execution Log table. There is no write access: rows are append-only. Contained within `integration_writer` — does not need to be assigned separately. |
| `snc_platform_rest_api_access` | Global (System) | Grants baseline technical access to ServiceNow REST Table API endpoints without granting record permissions. Contained within `integration_writer`. |

#### Cross-Scope API Privileges

The application runs with runtime access tracking set to **Enforcing** (set in the security fixes update set, and in `sdk-app/now.config.json` so an SDK build or deploy keeps it), so a script can only use a Global API that is listed here. Each privilege is used by a named script; anything else is refused at runtime.

| Privilege (`execute`) | Used by |
|---|---|
| `GlideRecordSecure.getValue` | S1.1 field access on `incident` |
| `Glide API: properties` | S1.3 eligibility and retry rules, event script action (`gs.getProperty`) |
| `Glide API: event management` | S1.3 eligibility rule (`gs.eventQueue`) |
| `Glide API: string utilities` | S1.3 eligibility rule (`gs.generateGUID`) |
| `ScriptableRESTMessageClient.setEndpoint` | S1.3 event script action |
| `ScriptableRESTMessageClient.setRequestBody` | S1.3 event script action |
| `ScriptableRESTMessageClient.execute` | S1.3 event script action |
| `ScriptableRESTResponse.getStatusCode` | S1.3 event script action |

Verified on a clean install (`dev434590`) by switching to Enforcing, running the eligibility rule through to the outbound call, and confirming that no further privilege was requested. The earlier read privileges on `sys_security_acl`, `sys_security_acl_role`, `sys_user_role` and `sys_scope`, and the execute privileges on `GlideRecord.insert` and `GlideRecord.setValue`, are removed: no app script uses them.

#### OAuth 2.0 Lifecycle & Mid-Run Recovery (FR-06)

The external orchestrator communicates using OAuth 2.0 Bearer tokens acquired via the password grant endpoint `/oauth_token.do`:

1. **Token Acquisition**: The orchestrator authenticates using `client_id`, `client_secret`, and service account credentials. The instance returns a Bearer access token valid for 1800 seconds (30 minutes).
2. **Pre-Flight Refresh (Proactive)**: If the token's remaining lifespan is below the buffer threshold (configurable, default 30 seconds), the orchestrator proactively refreshes before starting a multi-node pipeline.
3. **Mid-Run 401 Recovery (Reactive)**: If an API call receives an `HTTP 401 Unauthorized` during long-running execution due to clock drift or token invalidation, the harness re-authenticates and re-executes the transaction instead of failing or continuing silently.
4. **Invalid Token Rejection**: The API strictly enforces token validity; corrupt or expired tokens are rejected with `HTTP 401` and return zero data (`AUTH-04`).

---

### 3. Custom Table: AI Execution Log (`x_2215032_ai_inc_0_ai_execution_log`)

Per **FR-02**, every AI processing attempt—including successes, handled failures, and security-blocked actions—must generate an immutable execution log record.

#### Schema Dictionary

| Technical Column | Display Label | Data Type | Constraint / Value Range | Architectural Purpose |
|---|---|---|---|---|
| `incident_reference` | Incident Reference | Reference (`incident`) | **Mandatory**; Valid Incident `sys_id` (max_length: 32) | Foreign key binding the audit record to the parent incident. |
| `execution_id` | Execution ID | String (100) | **Mandatory**; Correlation trace string (e.g. `exec_<hex>`); **non-unique btree DB index** | **Indexed trace correlation key** for distributed tracing and multi-event lifecycle correlation. Intentionally non-unique to allow multi-step pipeline actions and retries to share an execution context while `sys_id` enforces record-level primary uniqueness. |
| `agent` | Agent | String (150) | **Mandatory**; Free text (e.g. `verification_harness`, `triage_agent`) | Attributes work to the exact agent / workflow node. |
| `action` | Action | Choice (40) | **Mandatory**; Choice: `read`, `execute`, `propose`, `escalate` | The specific operational action attempted. Enforced as a server-side choice list. |
| `status` | Status | Choice (40) | **Mandatory**; Choice: `started`, `succeeded`, `failed`, `blocked`, `awaiting_approval`, `abandoned` | Durable execution lifecycle outcome choices. Enforced as a server-side choice list. |
| `timestamp` | Timestamp | Date/Time (`glide_date_time`) | UTC format (`YYYY-MM-DD HH:MM:SS`) | Timestamp of execution start/event. |
| `result` | Result | String (5000) | Max 5000 characters | Diagnostic summary, classification output, or resolution suggestion. |
| `error` | Error | String (5000) | Max 5000 characters (blank on success) | Full diagnostic error message, stack trace, or reason for blockage. |

> [!NOTE]
> The table collection dictionary entry includes `enforce_dot_walk_cross_scope_access=true`, meaning cross-scope script dot-walking into this table's fields is explicitly enforced rather than relying on default scope isolation.

#### Row Model & Trace Correlation Architecture
The relationship between execution log rows and pipeline runs is formally defined as:
- **Primary Key vs. Trace Key**: ServiceNow's native `sys_id` serves as the unique primary key for every record. The `execution_id` is an **indexed trace correlation key** backed by a non-unique B-tree database index (`<unique_index>false</unique_index>`).
- **Distributed Trace Grouping**: Retaining a non-unique index on `execution_id` enables distributed tracing across multi-node or multi-event runs, allowing multiple audit entries (such as individual agent node actions or safe retry sequences) to correlate under a single parent execution trace without triggering database collision errors.
- **Lookup Invariant**: Single-event lookups can resolve individual rows, while trace queries by `execution_id` return the complete chronological trail of events for that pipeline execution.

> [!IMPORTANT]
> **Non-Unique `execution_id` by Architectural Design**:
> The `execution_id` field is intentionally **not unique** at the database schema level (`<unique_index>false</unique_index>`). It serves as an indexed correlation and distributed tracing key, enabling multi-step pipeline nodes, sub-agent tasks, and retry attempts for a single incident execution context to share the same trace ID. Record-level uniqueness is guaranteed exclusively by ServiceNow's native primary key (`sys_id`).

#### Append-Only Protection (SS7 / LOG-05)
To prevent rogue actors or automation bugs from tampering with audit records, the table enforces a strict **append-only policy**:
- **Delete ACL Rule**: `x_2215032_ai_inc_0_ai_execution_log` (operation: `delete`, sys_id: `7c036368471f4310c148497f316d433f`).
- **Role Binding**: Mapped exclusively to role **`admin`** (`sys_security_acl_role_20707445471b0710c148497f316d4371`). The table user role (`ai_execution_log_user`) and integration role (`integration_writer`) are completely excluded from delete access.
- **Artifact Alignment**: The exported update set (`ai_incident_orchestrator_s1_2.xml`) contains solely the `admin` role mapping for this delete ACL, eliminating artifact drift between the shipped XML and live instance behavior.
- **Observed Behavior**: When `ai_orchestrator_svc` executes `DELETE /api/now/table/x_2215032_ai_inc_0_ai_execution_log/{sys_id}`, ServiceNow responds with `HTTP 403 Forbidden` and preserves the record unaltered (`LOG-05` verified).

---

### 4. Least-Privilege Access Control List (ACL) Matrix

Permissions are bounded strictly to the minimal operational surface required for AI triage and logging:

```
+--------------------------------------------------------------------------------+
| ServiceNow Incident Table Security Perimeter                                   |
|                                                                                |
|  [PERMITTED WRITES by integration_writer]                                      |
|    * incident.work_notes              (also via task.work_notes inheritance)   |
|    * incident.x_2215032_ai_inc_0_ai_classification                             |
|    * incident.x_2215032_ai_inc_0_ai_human_review_required                      |
|    * incident.x_2215032_ai_inc_0_ai_suggestion                                 |
|    * incident.x_2215032_ai_inc_0_ai_resolution                                 |
|    * incident.x_2215032_ai_inc_0_ai_confidence                                 |
|    * incident.x_2215032_ai_inc_0_ai_failure_reason                             |
|    * incident.x_2215032_ai_inc_0_ai_processing_state                           |
|    * incident.x_2215032_ai_inc_0_ai_processing_start                           |
|    * incident.x_2215032_ai_inc_0_ai_processing_end                             |
|    * incident.x_2215032_ai_inc_0_ai_agent_version                              |
|    * incident.x_2215032_ai_inc_0_ai_model_name                                 |
|    * sys_journal_field               (read-only; incident work notes only)     |
|                                                                                |
|  All of the above only while ai_human_lock is false (record-level ACL).       |
|                                                                                |
|  [HUMAN-ONLY WRITES - admin only, AI blocked]                                  |
|    * incident.x_2215032_ai_inc_0_ai_human_lock  -> Emergency circuit breaker  |
|    * incident.x_2215032_ai_inc_0_ai_enabled     -> Human opt-in switch        |
|    * incident.x_2215032_ai_inc_0_ai_retry_count -> Set by the S1.3 rule only   |
|                                                                                |
|  [FORBIDDEN WRITES - No explicit ACL / global deny]                            |
|    * incident.state             -> Prevents unauthorized incident closure      |
|    * incident.assigned_to       -> Prevents re-assignment loops                |
|    * incident.assignment_group  -> Prevents routing hijack                     |
|    * incident.priority          -> Prevents unauthorized P1 escalations        |
|    * incident.comments          -> Prevents unreviewed customer-facing leaks   |
+--------------------------------------------------------------------------------+
```

#### Field Permission Rationale

| Field Target | `integration_writer` | `itil` / `admin` | Rationale |
|---|:---:|:---:|---|
| `incident.work_notes` | **ALLOW** (write) | ALLOW | Internal journal field. Orchestrator publishes diagnostic findings and runbooks visible only to ITIL fulfillers. ACL exists at both `incident.work_notes` and `task.work_notes` (parent class) levels. |
| `task.work_notes` | **ALLOW** (write) | ALLOW | Parent-class ACL that grants `work_notes` write via inheritance. Ensures coverage regardless of how the incident record is accessed. |
| `incident` (record) | **ALLOW** (write, only while `ai_human_lock=false`) | ALLOW | Record-level write for the integration role. The condition is the server-side safety stop (§4.1). Field ACLs below decide which fields it may change. |
| `sys_journal_field`, `sys_journal_field.*` | **ALLOW** (read, condition `name=incident^element=work_notes`) | ALLOW | Lets the orchestrator and harness confirm a work note persisted. Journals of other tables and incident `comments` stay unreadable (`DENY-07`). |
| `incident.x_2215032_ai_inc_0_ai_classification` | **ALLOW** | ALLOW (via `incident.*` wildcard) | LangGraph Classification Agent records incident category (`software`, `hardware`, etc.). |
| `incident.x_2215032_ai_inc_0_ai_human_review_required` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Triage agent flags complex or low-confidence incidents for manual operator inspection. |
| `incident.x_2215032_ai_inc_0_ai_suggestion` | **ALLOW** | ALLOW (via `incident.*` wildcard) | AI-generated remediation suggestion authored by the orchestrator. |
| `incident.x_2215032_ai_inc_0_ai_resolution` | **ALLOW** | ALLOW (via `incident.*` wildcard) | AI-authored resolution summary. |
| `incident.x_2215032_ai_inc_0_ai_confidence` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Confidence score (0–100) for the AI's classification or resolution decision. |
| `incident.x_2215032_ai_inc_0_ai_failure_reason` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Human-readable failure reason when AI processing is blocked or fails. |
| `incident.x_2215032_ai_inc_0_ai_processing_state` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Pipeline state machine value (e.g., `triaging`, `resolving`, `escalating`). |
| `incident.x_2215032_ai_inc_0_ai_processing_start` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Timestamp when AI pipeline began processing this incident. |
| `incident.x_2215032_ai_inc_0_ai_processing_end` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Timestamp when AI pipeline finished processing this incident. |
| `incident.x_2215032_ai_inc_0_ai_agent_version` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Version string of the AI agent/model that processed the incident. |
| `incident.x_2215032_ai_inc_0_ai_model_name` | **ALLOW** | ALLOW (via `incident.*` wildcard) | Name of the LLM model used (e.g., `gemini-2.5-pro`). |
| `incident.x_2215032_ai_inc_0_ai_human_lock` | **DENY** | **ALLOW** | **Emergency Circuit Breaker**. Writable only by `admin`. If `true`, all automated AI runs halt. The AI cannot unlock itself. |
| `incident.x_2215032_ai_inc_0_ai_enabled` | **DENY** | **ALLOW** | **Human Opt-In Switch**. Writable only by `admin`. Explicitly controls whether an incident is eligible for AI processing. The AI cannot opt tickets in. |
| `incident.x_2215032_ai_inc_0_ai_retry_count` | **DENY** | `admin` only | Incremented server-side by the S1.3 eligibility rule. Nobody else can reset the retry cap (`DENY-06`). |
| `incident.state` | **DENY** | ALLOW | Prevents autonomous agents from resolving or closing incidents without human approval. |
| `incident.assigned_to` | **DENY** | ALLOW | Prevents automated assignment loops or uncoordinated reassignment. |
| `incident.assignment_group` | **DENY** | ALLOW | Prevents ticket hijacking across support departments. |
| `incident.priority` | **DENY** | ALLOW | Prevents false-alarm P1 escalations that trigger organization-wide alerts. |
| `incident.comments` | **DENY** | ALLOW (condition: `caller_id = currentUser OR opened_by = currentUser AND incident_state NOT IN [7,8]`) | Customer-facing journal. Protects end users from raw, unverified AI outputs. |

> [!NOTE]
> **`incident.*` Wildcard ACL**: The Global-scope `incident.*` write ACL (from the ITSM Roles plugin, `sys_id: 91b7ec2cc3313010a282a539e540dd37`) allows `itil`/`admin` users to write any incident field if `caller_id = currentUser OR opened_by = currentUser AND incident_state != 7`. The 12 dedicated `integration_writer` field ACLs exist because `integration_writer` is **not** included in this wildcard — explicit field-by-field grants are required.

> [!NOTE]
> **Comments Protection — 100% ACL-Driven (Zero Business Rules Shipped)**:
> Customer comment protection is enforced strictly and exclusively by ServiceNow's native Access Control List (`incident.comments` write ACL, which restricts customer comments to `caller_id` or `opened_by` and excludes the service identity).
> 
> Under standard ServiceNow field-level ACL stripping semantics, incoming `PATCH` requests containing unauthorized fields (such as `comments`) return `HTTP 200 OK` while silently stripping the forbidden field (verified by `DENY-05` and `BULK-01`, confirming 0 journal entries created).
> 
> All experimental, inactive, or historical comment-intercepting Business Rules (such as `AI Block Customer Comments`, `AI Block Comments Journal Entry`, and destructive rules calling `deleteRecord()`) have been **completely purged** from the shipped update set artifact (`ai_incident_orchestrator_s1_2.xml`). No comment-blocking Business Rules exist or ship in this release, eliminating any risk of journal corruption or unexpected transaction aborts.

---

### 3.3 Out-of-the-Box (OOB) Platform ACLs & Role Bindings

In standard ServiceNow application packaging, scoped update sets capture role mappings (`sys_security_acl_role`) that bind the scoped role (`x_2215032_ai_inc_0.integration_writer`) to necessary platform capabilities. Baseline OOB ACLs themselves originate from ServiceNow core plugins (`com.snc.itsm`, `com.glide.task`, `com.snc.system_security`) and exist identically across instances; they are not duplicated inside the scoped update set to avoid schema collision.

These baseline platform ACLs are bound to `integration_writer` by role links shipped in the S1.2 update set. The ACLs themselves are not exported; they exist on every instance.

| Target | Operation | Baseline ACL `sys_id` | Role link `sys_id` | Effect |
|:---|:---:|:---|:---|:---|
| `incident.work_notes` | `read` | `a231390b870033000e56d61e36cb0bf3` | `e8636db4475f8310c148497f316d432f` | Read incident work notes. |
| `incident.work_notes` | `read` | `491482f053422010ad3cddeeff7b1245` | `28636db4475f8310c148497f316d4325` | Read incident work notes. |
| `task.work_notes` | `write` | `9d5e2504a52143108bb220b7a4d212e1` | `74d97fe847df4310c148497f316d43a4` | Append work notes through the `task` parent. |
| `task.work_notes` | `read` | `5d5e2504a52143108bb220b7a4d212df` | `64636db4475f8310c148497f316d4346` | Read work notes through the `task` parent. |

Record-level `incident` read and write come from the app's own scoped ACLs, not from baseline ACLs.

Three links from the original export are removed, because they were wrong on a clean install:

- **`2429f728…` on `24baff9a…`.** That ACL is a platform *deny-unless* rule. Adding a role to a deny-unless rule makes the role a requirement for everyone, so every `itil` user lost incident write. Verified on `dev434590`: `itil` could not write an incident until the link was removed.
- **`a90525b8…` on `e7c3abcc…`** (`audit_viewer` journal read). It let the integration role read every table's journal (#50). A scoped, conditioned read ACL replaces it.
- **`68632db4…` and `a4636db4…`.** They pointed at ACLs (`a4dee42c…`, `d785ac28…`) that were created by hand on `dev407364` and never exported, so a clean install could not commit them (#49).

> [!NOTE]
> **Integration Identity & OAuth Secrets Handling**:
> In accordance with ServiceNow security guidelines and credential cleanliness principles, local user accounts (`sys_user`), role assignments (`sys_user_has_role`), and OAuth Application Registry credentials (`oauth_entity`) represent instance-specific data and secrets. They are intentionally **excluded** from public Git update sets. An admin provisions them on each instance with the background script below (global scope), after filling in the password and client secret on the first line. Keep both out of the repository.
>
> ```javascript
> var PASSWORD = '<set on the instance>', CLIENT_SECRET = '<set on the instance>';
> var u = new GlideRecord('sys_user');
> if (!u.get('user_name', 'ai_orchestrator_svc')) {
>     u.initialize(); u.user_name = 'ai_orchestrator_svc'; u.first_name = 'AI'; u.last_name = 'Orchestrator Service';
>     u.web_service_access_only = true; u.active = true; u.insert();
> }
> u.user_password.setDisplayValue(PASSWORD); u.update();
> var role = new GlideRecord('sys_user_role'); role.get('name', 'x_2215032_ai_inc_0.integration_writer');
> var has = new GlideRecord('sys_user_has_role'); has.addQuery('user', u.sys_id); has.addQuery('role', role.sys_id); has.query();
> if (!has.next()) { has.initialize(); has.user = u.sys_id; has.role = role.sys_id; has.insert(); }
> var app = new GlideRecord('oauth_entity');
> if (!app.get('name', 'BARQ AI Orchestrator')) {
>     app.initialize(); app.name = 'BARQ AI Orchestrator'; app.type = 'client'; app.access_token_lifespan = 1800; app.active = true; app.insert();
> }
> app.client_secret.setDisplayValue(CLIENT_SECRET); app.update();
> gs.info('client_id=' + app.client_id);
> ```
>
> On a new PDI, also check that **Allow access to this table via web services** is on for `incident` (`sys_db_object.ws_access`). Some newer PDIs ship with it off, which makes every Table API call on `incident` fail with `403 Failed API level ACL Validation`, even for `admin`. After switching it on, the table's cached schema must be invalidated (`GlideTableManager.invalidateTable('incident')`) before the change takes effect.

---

### 4.1 Platform-Side Circuit Breaker: Human Lock Safety Stop

#### The Race Window Problem
The external AI orchestrator performs client-side inspection (`if incident.ai_human_lock: abort()`) before executing updates. However, because ServiceNow's REST Table API does not support optimistic concurrency or conditional updates (`ETag` / `If-Match`), a concurrency race condition exists:
1. Orchestrator reads `ai_human_lock == false`.
2. A human admin sets `ai_human_lock = true` on the incident form to take manual ownership.
3. Orchestrator issues `PATCH /api/now/table/incident/{sys_id}` with automated suggestions or notes.
4. Without server-side enforcement, ServiceNow commits the patch, overwriting data despite active human lock.

#### Platform Enforcement

The safety stop is the condition on the app's record-level write ACL for `incident`:

| ACL | Operation | Role | Condition |
|---|---|---|---|
| `incident` (scoped, `7de57cb2731b4b102aedfed25ab8b751`) | `write` | `x_2215032_ai_inc_0.integration_writer` | `x_2215032_ai_inc_0_ai_human_lock=false` |

ServiceNow evaluates the ACL on the server for every write, against the stored record. When a human has set AI Human Lock, the integration role has no write access to the incident at all, so a `PATCH` is refused with `HTTP 403` and no work note or AI field is written (`LOCK-03`). Humans are unaffected; they write through the platform's own incident ACLs.

The integration role cannot clear the lock itself: `incident.x_2215032_ai_inc_0_ai_human_lock` has its own write ACL for `admin` only (`LOCK-01`). The role choice is recorded in the S1.1 field model (#69).

Who can set the two human-controlled fields, checked on `dev434590` on 2026-09-17 against incident `INC0008001`:

| Identity | `ai_human_lock` | `ai_enabled` | `ai_retry_count` | Incident record |
|---|---|---|---|---|
| `admin` | writes (REST `PATCH` stored `true`/`false`, then restored) | writes | writes | writes |
| `itil` (stock demo user) | refused | refused | refused | writes |
| `ai_orchestrator_svc` | refused | refused | refused | writes while unlocked |

The `admin` row is a real `PATCH` over the Table API. The other two rows come from `GlideRecordSecure.canWrite()` in a background script that impersonates each user. The harness covers the `ai_orchestrator_svc` row (`LOCK-01`, `LOCK-02`, `DENY-06`). It has no admin credential by design, so the `admin` row is recorded here rather than tested by the harness.

> [!NOTE]
> **Why not a Business Rule.** The first version used a `before update` Business Rule calling `current.setAbortAction(true)`. Built inside the app scope, that rule fires but cannot abort a write to the Global `incident` table: on `dev434590` the rule ran, its conditions were all true, and the work note was still written. The earlier export worked around this by shipping the rule in the Global scope, which a clean install refuses to commit (#48). The ACL condition needs neither.

#### Defense-in-Depth Layering
| Layer | Control Mechanism | Protection Provided |
|---|---|---|
| **Layer 1: Field ACL** | `incident.x_2215032_ai_inc_0_ai_human_lock` (write ACL) | Integration service account cannot modify or clear the lock flag. Only `admin` can set/clear it. |
| **Layer 2: Client Orchestrator** | Pre-flight check in Python orchestrator | Avoids unneeded API calls when the incident is already known to be locked. |
| **Layer 3: Record ACL condition** | Scoped `incident` write ACL, `ai_human_lock=false` | Closes the race window. The server refuses the write if the lock is set when the `PATCH` arrives. |

---

### 5. Empirical Verification Matrix (100% PASS)

The test harness [`scripts/verify_permissions.py`](../../../scripts/verify_permissions.py) was run against a clean install of the four update sets (§8) on `dev434590.service-now.com`, using incident `INC0008001` (opened by another user, so creator-based ACLs cannot grant access). The same harness on `dev407364` passes 27 of 28 until the §8 update steps are applied there; `DENY-07` fails because the journal read is not yet narrowed. Every test performs real HTTP transactions with read-after-write database queries.

#### Complete Test Run Results (28 of 28 Passed)

| Test ID | Category | Target / Operation | Expected Behavior | Observed Result | Status |
|:---|:---|:---|:---|:---|:---:|
| **AUTH-01** | Authentication | `POST /oauth_token.do` | 200 OK + Bearer access token issued | HTTP 200 (Lifespan: 1799s) | **PASS** |
| **AUTH-02** | Authentication | `GET /api/now/table/sys_user` (`gs.getUserID()`) | Authenticated session token belongs to expected service account | `user_name=ai_orchestrator_svc` | **PASS** |
| **AUTH-03** | Authentication | `GET /api/now/table/sys_user_has_role` | Non-admin verification (403 Forbidden) | HTTP 403 (No `security_admin`) | **PASS** |
| **AUTH-04** | Authentication | `GET /api/now/table/incident` (Invalid token) | Invalid/expired token rejected | HTTP 401 Unauthorized | **PASS** |
| **TOKEN-01**| Token Lifecycle | Mid-run expiry detection & recovery | Harness catches 401 on stale token, re-authenticates, and recovers | Re-auth recovery HTTP 200 | **PASS** |
| **PERM-01** | Permitted | `GET /api/now/table/incident/{id}` | Read incident record | HTTP 200 (`INC0010003`) | **PASS** |
| **PERM-02** | Permitted | `PATCH incident.work_notes` | Persisted in `sys_journal_field` | HTTP 200 (Count = 1) | **PASS** |
| **PERM-03** | Permitted | `PATCH incident.ai_classification` | Write scoped AI classification field | HTTP 200 (Value: `software`) | **PASS** |
| **PERM-04** | Permitted | `PATCH incident.ai_human_review_required` | Write scoped human review flag | HTTP 200 (Value: `true`) | **PASS** |
| **LOG-01**  | Execution Log | `POST x_..._ai_execution_log` (`succeeded`) | Status `succeeded` audit record created | HTTP 201 Created | **PASS** |
| **LOG-02**  | Execution Log | `POST x_..._ai_execution_log` (`failed`) | Status `failed` audit record created | HTTP 201 Created | **PASS** |
| **LOG-03**  | Execution Log | `POST x_..._ai_execution_log` (`blocked`) | Status `blocked` audit record created | HTTP 201 Created | **PASS** |
| **LOG-06**  | Execution Log | `POST x_..._ai_execution_log` (`abandoned`) | Status `abandoned` audit record created | HTTP 201 Created | **PASS** |
| **LOG-04**  | Execution Log | `GET x_..._ai_execution_log?execution_id=` | Indexed lookup resolves trace records (non-unique index; uniqueness via `sys_id`) | HTTP 200 (1 record returned) | **PASS** |
| **LOG-05**  | Execution Log | `DELETE x_..._ai_execution_log/{id}` | Append-only: Delete blocked with 403 | HTTP 403 (Record exists) | **PASS** |
| **LOG-07**  | Execution Log | `PATCH x_..._ai_execution_log/{id}` | Append-only: update refused | Record unchanged | **PASS** |
| **LOG-08**  | Execution Log | Two `POST`s sharing one `execution_id` | Both accepted; lookup returns 2 (non-unique trace key) | HTTP 201, 201; 2 rows | **PASS** |
| **DENY-01** | Forbidden | `PATCH incident.state` | State modification rejected | HTTP 200 (State unchanged `1`) | **PASS** |
| **DENY-02** | Forbidden | `PATCH incident.assigned_to` | Assignment modification rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-03** | Forbidden | `PATCH incident.assignment_group` | Group reassignment rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-04** | Forbidden | `PATCH incident.priority` | Priority escalation rejected | HTTP 200 (Priority unchanged `5`)| **PASS** |
| **DENY-05** | Forbidden | `PATCH incident.comments` | Customer comments stripped from journal | HTTP 200 (0 journal entries) | **PASS** |
| **DENY-06** | Forbidden | `PATCH incident.ai_retry_count` | Retry count unchanged | HTTP 200 (value unchanged) | **PASS** |
| **DENY-07** | Forbidden | `GET sys_journal_field` (other tables, incident comments) | Nothing readable outside incident work notes | 0 rows each | **PASS** |
| **LOCK-01** | Human Lock | `PATCH incident.ai_human_lock` | Circuit breaker modification rejected | HTTP 200 (Lock unchanged `false`)| **PASS** |
| **LOCK-02** | Human Lock | `PATCH incident.ai_enabled` | Opt-in switch modification rejected | HTTP 200 (Enabled unchanged `false`)| **PASS** |
| **LOCK-03** | Human Lock | `PATCH incident/{sys_id}` (locked incident) | Server refuses the automated update | HTTP 403 (0 journal entries) | **PASS** |
| **BULK-01** | Bulk Bypass | `PATCH incident` (Mixed payload) | Permitted written, all forbidden stripped | `work_notes` wrote; rest blocked | **PASS** |

> [!NOTE]
> **ServiceNow Field-Level Stripping Semantics**: When a client sends a `PATCH` request containing forbidden fields, ServiceNow returns `HTTP 200 OK` while silently stripping unauthorized fields in accordance with ACL rules. The verification harness never relies on HTTP status codes alone; every test performs an independent read-after-write GET request against the database and `sys_journal_field` to prove that forbidden values were never persisted.

#### 5.1 Safety Stop Live Verification & Anti-Fail-Open Harness Hardening

In response to architectural review on test harness integrity, the `LOCK` test suite was hardened against false-positive / fail-open vulnerabilities:

1. **Elimination of Fail-Open Fallback (`LOCK-03`)**:
   - In earlier iterations, if no incident had `ai_human_lock=true`, the harness returned a provisional `PASS`, so the suite could pass without exercising the safety stop.
   - **Fix**: The fallback branch now explicitly returns `verdict="FAIL"`. The test strictly requires an active, locked incident on the target instance.
   - **Live Evidence**: Incident `INC0010041` (`sys_id: 0db9760f47870b10c148497f316d4320`) was locked via `ai_human_lock=true` by the administrator on `dev407364`. When `ai_orchestrator_svc` attempted an automated patch with `work_notes`, the server refused the transaction with `HTTP 403 Forbidden` and 0 entries in `sys_journal_field`.

2. **Hardened Invariant Checks for Baseline and Post-Patch GET Queries (`LOCK-01`, `LOCK-02`)**:
   - Previously, baseline and post-update state checks defaulted `before` and `after` to `"false"` on missing keys or failed queries, meaning an HTTP error on GET could evaluate `after == before` and produce an accidental `PASS`.
   - **Fix**: Pre-patch and post-patch GET requests now enforce `status_code == 200`. Any network, authorization, or schema failure on read immediately fails the test with explicit diagnostic context. Furthermore, the test validates `after != attempt` to guarantee the requested change did not land.

3. **Tightened Journal Verification Query (`LOCK-03`)**:
   - If the verification query to `sys_journal_field` returned an error or was denied, an unhardened check could interpret an empty result list as zero journal entries (`journal_count == 0`), falsely satisfying the abort criteria.
   - **Fix**: The query now explicitly enforces `chk.status_code == 200` alongside `patch_r.status_code in (400, 403)` and `journal_count == 0`. All conditions must hold simultaneously for `LOCK-03` to pass.

---

### 6. Repository Credential Cleanliness (FR-07)

All credentials and sensitive configuration adhere to strict hygiene:
- Credentials reside solely in local, git-ignored `.env` files.
- Tracked configuration (`src/app/core/config.py`) defines settings via Pydantic `SecretStr` models with zero hardcoded credentials or defaults. This became true when #139 removed the default bearer token; the required `SecretStr` is asserted by `tests/auth/test_config_required_secrets.py`.
- GitHub secret scanning and push protection are enabled; they detect known provider token formats, not generic secrets such as passwords or client secrets.

---

### 7. Reproduction & Execution Instructions

To execute the automated security verification suite against the live instance:

```powershell
# 1. Ensure dependencies are installed
uv sync

# 2. Execute the verification harness
uv run python scripts/verify_permissions.py
```

Upon completion, the harness generates:
1. Terminal stdout detailing the empirical proof for each test case.
2. Machine-readable audit artifact: [`scripts/verification_report.json`](../../../scripts/verification_report.json).
3. Exit code `0` on 100% pass rate; exit code `1` if any violation or leak occurs.

---

### 8. ServiceNow Artifact Package & Import

| Artifact | Contents |
|---|---|
| [`ai_incident_orchestrator_s1_1.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml) | Scoped application and the 13 AI fields on `incident`. |
| [`ai_incident_orchestrator_s1_2.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_2.xml) | Execution log table, `integration_writer` and `ai_execution_log_user` roles, field ACLs, append-only delete ACL. |
| [`ai_incident_orchestrator_s1_3.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_3.xml) | S1.3 eligibility and retry rules, outbound event. |
| [`ai_incident_orchestrator_s1_2_security_fixes.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_2_security_fixes.xml) | Record-level incident ACL with the lock condition, create-only log field ACL, narrowed journal read, `ai_retry_count` ACL, removed role links, trimmed cross-scope privileges, runtime access tracking set to Enforcing. |

None of the four contains a Global-scope record; `tests/repo/test_no_new_global_scope_in_exports.py` fails the build if one appears.

#### Import order

Import each file under **System Update Sets > Retrieved Update Sets > Import Update Set from XML**, then **Preview** and **Commit**, in this order:

1. `ai_incident_orchestrator_s1_1.xml`
2. `ai_incident_orchestrator_s1_2.xml`
3. `ai_incident_orchestrator_s1_3.xml`
4. `ai_incident_orchestrator_s1_2_security_fixes.xml`

The S1.3 preview reports one duplicate `GlideRecord.setValue` privilege; it is removed by step 4 and can be skipped. Then provision the integration user and OAuth client (§2) and run `uv run python scripts/verify_permissions.py`.

Verified on a clean PDI (`dev434590`, 2026-09-16): all four committed, and the harness passed **28 / 28** ([report](clean-install-verification-report.json)).

#### Updating an instance that has the earlier S1.2 set

`dev407364` was configured by hand before the export was cleaned up, so it holds Global changes that no scoped update set can undo. After importing step 4 there, an admin runs this once as a background script (global scope):

```javascript
// Re-enable the platform deny-unless read rule on sys_journal_field (it was deactivated).
var acl = new GlideRecord('sys_security_acl');
if (acl.get('7fa32d4e9f6012103b32602e9a0a1c36')) { acl.active = true; acl.update(); }

// The lock is now enforced by the scoped ACL condition; remove the Global rule.
var br = new GlideRecord('sys_script');
if (br.get('2f5038f9475f8b10c148497f316d43f5')) { br.deleteRecord(); }

// Hand-made Global ACLs that the scoped ACLs replace.
['a4dee42c47170310c148497f316d4336', 'd785ac2847d30310c148497f316d439e'].forEach(function (id) {
    var custom = new GlideRecord('sys_security_acl');
    if (custom.get(id)) { custom.deleteRecord(); }
});
```

Step 4 also deletes a duplicate `admin` role link on the Human Lock write ACL (`c3f8aba4…`); the re-exported S1.2 set no longer contains it.

The other three Global ACLs in the earlier export (`a2a3e94e…`, `91b7ec2c…`, `66f0fbc6…`) match the platform baseline in content and need no change. Afterwards, re-run the harness: `DENY-07` fails on `dev407364` until the journal changes are in place.
