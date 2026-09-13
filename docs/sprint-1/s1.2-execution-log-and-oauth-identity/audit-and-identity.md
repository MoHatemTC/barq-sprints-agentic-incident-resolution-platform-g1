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
- **Verification Status**: **25 / 25 PASS (100% Verified Empirical Compliance)**

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
| `x_2215032_ai_inc_0.ai_execution_log_user` | Scoped (`x_2215032_ai_inc_0`) | Grants create, read, and write access to the AI Execution Log table. Contained within `integration_writer` — does not need to be assigned separately. |
| `snc_platform_rest_api_access` | Global (System) | Grants baseline technical access to ServiceNow REST Table API endpoints without granting record permissions. Contained within `integration_writer`. |

#### Cross-Scope API Privileges

Because the app runs in a private scope (`x_2215032_ai_inc_0`) but must interact with Global-scope tables and APIs, three explicit `sys_scope_privilege` grants are included in the update set:

| Privilege | Target | Operation | Purpose |
|---|---|---|---|
| `GlideRecord.setValue` | Global (`scriptable`) | `execute` | Allows scoped Business Rules and scripts to call `GlideRecord.setValue()` on Global-scope records (e.g., setting incident fields via server-side scripts). |
| `sys_security_acl` | Global (`sys_db_object`) | `read` | Allows the scoped application to inspect ACL records when enforcing field-level security decisions. |
| `sys_scope` | Global (`sys_db_object`) | `read` | Allows the scoped application to read scope metadata (required for cross-scope validation). |

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
| `incident_reference` | Incident Reference | Reference (`incident`) | Valid Incident `sys_id` (max_length: 32, sys_id storage) | Foreign key binding the audit record to the parent incident. |
| `execution_id` | Execution ID | String (100) | Unique string (e.g. `exec_verify_<hex>`); **non-unique btree DB index** for fast lookup | **Indexed trace key** for distributed tracing and single-record lookup. Non-unique to allow batch operations with shared execution context. |
| `agent` | Agent | String (150) | Free text (e.g. `verification_harness`, `triage_agent`) | Attributes work to the exact agent / workflow node. |
| `action` | Action | Choice (40) | Choice: `read`, `execute`, `propose`, `escalate` | The specific operational action attempted. Enforced as a server-side choice list. |
| `status` | Status | Choice (40) | Choice: `started`, `succeeded`, `failed`, `blocked`, `awaiting_approval`, `abandoned` | Durable execution lifecycle outcome choices. Enforced as a server-side choice list. |
| `timestamp` | Timestamp | Date/Time (`glide_date_time`) | UTC format (`YYYY-MM-DD HH:MM:SS`) | Timestamp of execution start/event. |
| `result` | Result | String (5000) | Max 5000 characters | Diagnostic summary, classification output, or resolution suggestion. |
| `error` | Error | String (5000) | Max 5000 characters (blank on success) | Full diagnostic error message, stack trace, or reason for blockage. |

> [!NOTE]
> The table collection dictionary entry includes `enforce_dot_walk_cross_scope_access=true`, meaning cross-scope script dot-walking into this table's fields is explicitly enforced rather than relying on default scope isolation.

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
|    * sys_journal_field.*             (read-only; enables work_notes verify)    |
|                                                                                |
|  [HUMAN-ONLY WRITES - itil/admin only, AI blocked]                             |
|    * incident.x_2215032_ai_inc_0_ai_human_lock  -> Emergency circuit breaker  |
|    * incident.x_2215032_ai_inc_0_ai_enabled     -> Human opt-in switch        |
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
| `sys_journal_field.*` | **ALLOW** (read) | ALLOW | Wildcard read ACL on the journal field table. Enables the orchestrator and harness to verify that `work_notes` writes persisted. Does **not** grant write access to `comments`. |
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
| `incident.x_2215032_ai_inc_0_ai_human_lock` | **DENY** | **ALLOW** | **Emergency Circuit Breaker**. Writable only by `itil`/`admin`. If `true`, all automated AI runs halt. The AI cannot unlock itself. |
| `incident.x_2215032_ai_inc_0_ai_enabled` | **DENY** | **ALLOW** | **Human Opt-In Switch**. Writable only by `itil`/`admin`. Explicitly controls whether an incident is eligible for AI processing. The AI cannot opt tickets in. |
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

### 4.1 Platform-Side Circuit Breaker: Human Lock Safety Stop (Business Rule)

#### The Race Window Problem
The external AI orchestrator performs client-side inspection (`if incident.ai_human_lock: abort()`) before executing updates. However, because ServiceNow's REST Table API does not support optimistic concurrency or conditional updates (`ETag` / `If-Match`), a concurrency race condition exists:
1. Orchestrator reads `ai_human_lock == false`.
2. A human ITIL agent toggles `ai_human_lock = true` on the incident form to take manual ownership.
3. Orchestrator issues `PATCH /api/now/table/incident/{sys_id}` with automated suggestions or notes.
4. Without server-side enforcement, ServiceNow commits the patch, overwriting data despite active human lock.

#### Platform Enforcement Specification
To guarantee defense-in-depth, a platform-side `before-update` Business Rule is deployed on the `incident` table:

* **Business Rule Name**: `AI Enforce Human Lock Safety Stop`
* **Table**: `Incident [incident]`
* **When**: `before`
* **Operation**: `action_update = true`
* **Order**: `50` (executes prior to default business rules)
* **Application Scope**: `Global` (or `AI Incident Orchestrator` with cross-scope privilege)
* **Enforcement Logic**:
  ```javascript
  (function executeRule(current, previous /*null when async*/) {

      // 1. Check if human lock is active
      var lockVal = '' + current.getValue('x_2215032_ai_inc_0_ai_human_lock');
      if (lockVal !== '1' && lockVal !== 'true') {
          return;
      }

      // 2. Only enforce for the AI integration identity
      var isAiCaller = (gs.getUserName() == 'ai_orchestrator_svc') ||
                       gs.hasRole('x_2215032_ai_inc_0.integration_writer');

      if (!isAiCaller) {
          return;
      }

      // 3. The permitted AI write fields (including work_notes)
      var aiFields = [
          'work_notes',
          'x_2215032_ai_inc_0_ai_classification',
          'x_2215032_ai_inc_0_ai_confidence',
          'x_2215032_ai_inc_0_ai_suggestion',
          'x_2215032_ai_inc_0_ai_resolution',
          'x_2215032_ai_inc_0_ai_human_review_required',
          'x_2215032_ai_inc_0_ai_failure_reason',
          'x_2215032_ai_inc_0_ai_processing_state',
          'x_2215032_ai_inc_0_ai_processing_start',
          'x_2215032_ai_inc_0_ai_processing_end',
          'x_2215032_ai_inc_0_ai_agent_version',
          'x_2215032_ai_inc_0_ai_model_name'
      ];

      // 4. Reject if any AI field was modified in this update
      for (var i = 0; i < aiFields.length; i++) {
          var field = aiFields[i];
          if (current.isValidField(field)) {
              var elem = current.getElement(field);
              if (elem != null && elem.changes()) {
                  gs.addErrorMessage(
                      'Incident is locked by a human operator (AI Human Lock). Automated AI update to ' + field + ' rejected.'
                  );
                  current.setAbortAction(true);
                  return;
              }
          }
      }

  })(current, previous);
  ```

> [!NOTE]
> **Scope & Platform Architecture Rationale**:
> - **Scoped Application Boundary**: All custom data assets—including the 13 `x_2215032_ai_inc_0_*` fields, the `x_2215032_ai_inc_0_ai_execution_log` table, the `integration_writer` role, and all field ACLs—reside strictly inside the scoped application `AI Incident Orchestrator` (`x_2215032_ai_inc_0`). No custom tables or custom fields reside in Global scope.
> - **Global Table Context for `incident` Business Rule**: The `incident` table is an out-of-the-box (OOB) platform table living natively in the `Global` scope. When external orchestrators submit updates via the ServiceNow REST Table API (`PATCH /api/now/table/incident/{sys_id}`), the request pipeline executes in the Global transaction context. Under ServiceNow's Application Isolation architecture, scoped Business Rules attached to Global tables are restricted from aborting incoming Global Table API transactions unless deployed with native Global execution rights. Placing this specific safety stop Business Rule on the `incident` table ensures that `current.setAbortAction(true)` immediately halts the REST transaction (returning HTTP 403) and prevents automated overwrite of locked incidents.

#### Defense-in-Depth Layering
| Layer | Control Mechanism | Protection Provided |
|---|---|---|
| **Layer 1: Field ACL** | `incident.x_2215032_ai_inc_0_ai_human_lock` (write ACL) | Integration service account cannot modify or clear the lock flag. Only `itil` and `admin` can set/clear it. |
| **Layer 2: Client Orchestrator** | Pre-flight check in Python orchestrator | Avoids unneeded API calls when the incident is already known to be locked. |
| **Layer 3: Platform Business Rule** | `AI Enforce Human Lock Safety Stop` (`before-update`) | Closes the race window. Rejects incoming `PATCH` requests on the server side via `current.setAbortAction(true)` if `ai_human_lock == true`. |

---

### 5. Empirical Verification Matrix (100% PASS)

The test harness [`scripts/verify_permissions.py`](../../../scripts/verify_permissions.py) was executed against live ServiceNow instance `dev407364.service-now.com` using target incident `INC0010003` (opened by a third party to prevent creator-privilege bias). Every test performs real HTTP transactions with read-after-write database queries.

#### Complete Test Run Results (25 of 25 Passed)

| Test ID | Category | Target / Operation | Expected Behavior | Observed Result | Status |
|:---|:---|:---|:---|:---|:---:|
| **AUTH-01** | Authentication | `POST /oauth_token.do` | 200 OK + Bearer access token issued | HTTP 200 (Lifespan: 1799s) | **PASS** |
| **AUTH-02** | Authentication | `GET /api/now/table/sys_user` | Authenticated identity matches service account | `user_name=ai_orchestrator_svc` | **PASS** |
| **AUTH-03** | Authentication | `GET /api/now/table/sys_user_has_role` | Non-admin verification (403 Forbidden) | HTTP 403 (No `security_admin`) | **PASS** |
| **AUTH-04** | Authentication | `GET /api/now/table/incident` (Invalid token) | Invalid/expired token rejected | HTTP 401 Unauthorized | **PASS** |
| **TOKEN-01**| Token Lifecycle | Mid-run expiry detection | Harness detects 401 and re-authenticates | HTTP 401 caught & handled | **PASS** |
| **PERM-01** | Permitted | `GET /api/now/table/incident/{id}` | Read incident record | HTTP 200 (`INC0010003`) | **PASS** |
| **PERM-02** | Permitted | `PATCH incident.work_notes` | Persisted in `sys_journal_field` | HTTP 200 (Count = 1) | **PASS** |
| **PERM-03** | Permitted | `PATCH incident.ai_classification` | Write scoped AI classification field | HTTP 200 (Value: `software`) | **PASS** |
| **PERM-04** | Permitted | `PATCH incident.ai_human_review_required` | Write scoped human review flag | HTTP 200 (Value: `true`) | **PASS** |
| **LOG-01**  | Execution Log | `POST x_..._ai_execution_log` (`succeeded`) | Status `succeeded` audit record created | HTTP 201 Created | **PASS** |
| **LOG-02**  | Execution Log | `POST x_..._ai_execution_log` (`failed`) | Status `failed` audit record created | HTTP 201 Created | **PASS** |
| **LOG-03**  | Execution Log | `POST x_..._ai_execution_log` (`blocked`) | Status `blocked` audit record created | HTTP 201 Created | **PASS** |
| **LOG-06**  | Execution Log | `POST x_..._ai_execution_log` (`abandoned`) | Status `abandoned` audit record created | HTTP 201 Created | **PASS** |
| **LOG-04**  | Execution Log | `GET x_..._ai_execution_log?execution_id=` | Indexed query returns exactly 1 record | HTTP 200 (1 record returned) | **PASS** |
| **LOG-05**  | Execution Log | `DELETE x_..._ai_execution_log/{id}` | Append-only: Delete blocked with 403 | HTTP 403 (Record exists) | **PASS** |
| **DENY-01** | Forbidden | `PATCH incident.state` | State modification rejected | HTTP 200 (State unchanged `1`) | **PASS** |
| **DENY-02** | Forbidden | `PATCH incident.assigned_to` | Assignment modification rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-03** | Forbidden | `PATCH incident.assignment_group` | Group reassignment rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-04** | Forbidden | `PATCH incident.priority` | Priority escalation rejected | HTTP 200 (Priority unchanged `5`)| **PASS** |
| **DENY-05** | Forbidden | `PATCH incident.comments` | Customer comments stripped from journal | HTTP 200 (0 journal entries) | **PASS** |
| **LOCK-01** | Human Lock | `PATCH incident.ai_human_lock` | Circuit breaker modification rejected | HTTP 200 (Lock unchanged `false`)| **PASS** |
| **LOCK-02** | Human Lock | `PATCH incident.ai_enabled` | Opt-in switch modification rejected | HTTP 200 (Enabled unchanged `false`)| **PASS** |
| **LOCK-03** | Human Lock | `PATCH incident/{sys_id}` (locked `INC0010041`) | Platform Business Rule aborts automated update | HTTP 403 (0 journal entries) | **PASS** |
| **BULK-01** | Bulk Bypass | `PATCH incident` (Mixed payload) | Permitted written, all forbidden stripped | `work_notes` wrote; rest blocked | **PASS** |
| **CRED-01** | Cleanliness | Repository Secret Scan | Zero secrets/passwords in tracked files | 93 files scanned clean | **PASS** |

> [!NOTE]
> **ServiceNow Field-Level Stripping Semantics**: When a client sends a `PATCH` request containing forbidden fields, ServiceNow returns `HTTP 200 OK` while silently stripping unauthorized fields in accordance with ACL rules. The verification harness never relies on HTTP status codes alone; every test performs an independent read-after-write GET request against the database and `sys_journal_field` to prove that forbidden values were never persisted.

#### 5.1 Safety Stop Live Verification & Anti-Fail-Open Harness Hardening

In response to architectural review on test harness integrity, the `LOCK` test suite was hardened against false-positive / fail-open vulnerabilities:

1. **Elimination of Fail-Open Fallback (`LOCK-03`)**:
   - In earlier iterations, if no incident had `ai_human_lock=true`, the harness returned a provisional `PASS`. This fail-open shape meant the test suite could report 25/25 without actually exercising the server-side Business Rule.
   - **Fix**: The fallback branch now explicitly returns `verdict="FAIL"`. The test strictly requires an active, locked incident on the target instance.
   - **Live Evidence**: Incident `INC0010041` (`sys_id: 0db9760f47870b10c148497f316d4320`) was locked via `ai_human_lock=true` by the administrator on `dev407364`. When `ai_orchestrator_svc` attempted an automated patch with `work_notes`, the `AI Enforce Human Lock Safety Stop` Business Rule immediately aborted the transaction with `HTTP 403 Forbidden` and 0 entries in `sys_journal_field`.

2. **Hardened Invariant Checks for Baseline and Post-Patch GET Queries (`LOCK-01`, `LOCK-02`)**:
   - Previously, baseline and post-update state checks defaulted `before` and `after` to `"false"` on missing keys or failed queries, meaning an HTTP error on GET could evaluate `after == before` and produce an accidental `PASS`.
   - **Fix**: Pre-patch and post-patch GET requests now enforce `status_code == 200`. Any network, authorization, or schema failure on read immediately fails the test with explicit diagnostic context. Furthermore, the test validates `after != attempt` to guarantee the requested change did not land.

3. **Tightened Journal Verification Query (`LOCK-03`)**:
   - If the verification query to `sys_journal_field` returned an error or was denied, an unhardened check could interpret an empty result list as zero journal entries (`journal_count == 0`), falsely satisfying the abort criteria.
   - **Fix**: The query now explicitly enforces `chk.status_code == 200` alongside `patch_r.status_code in (400, 403)` and `journal_count == 0`. All conditions must hold simultaneously for `LOCK-03` to pass.

---

### 6. Repository Credential Cleanliness (FR-07 / CRED-01)

All credentials and sensitive configuration adhere to strict hygiene:
- Credentials reside solely in local, git-ignored `.env` files.
- Tracked configuration (`src/app/core/config.py`) defines settings via Pydantic `SecretStr` models with zero hardcoded credentials or defaults.
- The automated repository scanner (`CRED-01`) recursively analyzes all tracked source, markdown, YAML, JSON, and TOML files against high-entropy regex patterns, verifying zero committed passwords, API keys, client secrets, or Basic auth tokens across all 93 tracked repository files.

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

### 8. ServiceNow Artifact Package & Unified Import

All S1.2 deliverables are packaged into a single, comprehensive Update Set XML file:

| Deliverable | Artifact File | Type | Target Scope | Contents & Invariants |
|---|---|---|---|---|
| **Sprint 1.1 Foundation** | [`ai_incident_orchestrator_s1_1.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml) | Remote Update Set | Scoped (`x_2215032_ai_inc_0`) | Scoped application container and the **13 custom fields** on `incident` (including `ai_human_lock`, `ai_enabled`, `ai_classification`). |
| **Sprint 1.2 Unified Deliverable** | [`ai_incident_orchestrator_s1_2.xml`](../../../servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_2.xml) | Remote Update Set | Scoped (`x_2215032_ai_inc_0`) | **All-in-one package** containing:<br>1. **`ai_execution_log`** table & 8 dictionary fields<br>2. **`integration_writer`** & `ai_execution_log_user` roles<br>3. **Field-level write ACLs** for 12 scoped fields + `work_notes`<br>4. **Append-only delete ACL** mapped strictly to `admin`<br>5. **`AI Enforce Human Lock Safety Stop`** Business Rule on `incident` |

#### Step-by-Step Import Instructions
1. **Import S1.1**:
   - In ServiceNow, navigate to **System Update Sets** > **Retrieved Update Sets**.
   - Click **Import Update Set from XML**, upload `ai_incident_orchestrator_s1_1.xml`.
   - Open the retrieved update set, click **Preview Update Set**, then click **Commit Update Set**.
2. **Import S1.2**:
   - Return to **Retrieved Update Sets**, click **Import Update Set from XML**, upload `ai_incident_orchestrator_s1_2.xml`.
   - Open the retrieved update set, click **Preview Update Set**, then click **Commit Update Set**.
   - This single action installs the table, all security boundaries, and the live safety stop Business Rule.
3. **Verify**:
   - Run `uv run python scripts/verify_permissions.py` → **25 / 25 PASS**.

