# Sprint 1 (Task S1.2): ServiceNow Security & Audit Foundation
## Identity, Least-Privilege Access Control, Append-Only Execution Log & Verification

---

### Document Status & Metadata

- **Work Item**: Sprint 1 — Task S1.2: AI Execution Log Table, OAuth Integration Identity & Least-Privilege ACLs
- **Application Scope**: `AI Incident Orchestrator` (`x_2215032_ai_inc_0`)
- **Service Account**: `ai_orchestrator_svc` (Machine identity, non-admin, Web Service Access Only)
- **Primary Integration Role**: `x_2215032_ai_inc_0.integration_writer`
- **Target Instance**: `dev407364.service-now.com`
- **Verification Harness**: [`scripts/verify_permissions.py`](../../../scripts/verify_permissions.py)
- **Machine-Readable Report**: [`scripts/verification_report.json`](../../../scripts/verification_report.json)
- **Verification Status**: **21 / 21 PASS (100% Verified Empirical Compliance)**

---

### 1. Executive Summary & Architectural Overview

Task S1.2 delivers the foundational security, identity, and audit infrastructure for the AI Incident Resolution Platform. Built under strict zero-trust principles and ServiceNow private-scope isolation, the deliverable unites three core architectural pillars:

1. **Dedicated Non-Admin Service Identity (FR-06)**: An automated machine account (`ai_orchestrator_svc`) authenticating exclusively via OAuth 2.0. The account holds no administrative, security admin, or ITIL fulfiller roles, eliminating privilege creep.
2. **Append-Only AI Execution Log Table (FR-02)**: A scoped audit repository (`x_2215032_ai_inc_0_ai_execution_log`) that records every processing attempt (success, failure, or blocked state) with indexed execution IDs for tamper-proof trace correlation. Deletion is cryptographically/operationally prohibited.
3. **Least-Privilege Field-Level ACL Boundaries**: Explicit Access Control Lists that grant the integration account write access strictly to internal diagnostic work notes and agent-authored scoped AI fields (`AI Classification`), while enforcing hard, unbypassable denials on incident lifecycle state, assignments, customer comments, priority, and human-lock safety controls.

```mermaid
flowchart TD
    subgraph External["External AI Orchestrator (Python Runtime)"]
        Harness["verify_permissions.py / Orchestrator Worker"]
        TokenClient["OAuth Token Client"]
    end

    subgraph ServiceNow["ServiceNow PDI (Instance Scope: x_2215032_ai_inc_0)"]
        OAuthEP["OAuth Endpoint (/oauth_token.do)"]
        TableAPI["ServiceNow Table API"]
        
        subgraph Security["Access Control Layer (ACLs)"]
            TableACL["Incident Table Write ACL"]
            FieldACLs["Field-Level Write ACLs"]
            LogDeleteACL["Execution Log Delete ACL (Deny)"]
        end

        subgraph Storage["Persisted Records"]
            IncTable["incident Table"]
            WorkNotes["sys_journal_field (work_notes)"]
            AIFields["x_2215032_ai_inc_0_* (AI Fields)"]
            AuditLog["x_2215032_ai_inc_0_ai_execution_log (Append-Only)"]
        end
    end

    TokenClient -->|OAuth Password Grant| OAuthEP
    OAuthEP -->|JWT Bearer Token| TokenClient
    Harness -->|Bearer Token HTTP Calls| TableAPI
    TableAPI --> Security
    Security -->|PERMITTED: Internal Diagnostic| WorkNotes
    Security -->|PERMITTED: Classification Write| AIFields
    Security -->|PERMITTED: Audit Insert| AuditLog
    Security -->|BLOCKED: state, assigned_to, priority, comments, human_lock| IncTable
    Security -->|BLOCKED: DELETE attempt (HTTP 403)| LogDeleteACL
```

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
| `x_2215032_ai_inc_0.integration_writer` | Scoped (`x_2215032_ai_inc_0`) | Grants write permission to permitted scoped incident fields (`AI Classification`) and table-level write boundary. |
| `x_2215032_ai_inc_0.ai_execution_log_user` | Scoped (`x_2215032_ai_inc_0`) | Grants create and read access to the AI Execution Log table. |
| `snc_platform_rest_api_access` | Global (System) | Grants baseline technical access to ServiceNow REST Table API endpoints without granting record permissions. |

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
| `incident_reference` | Incident Reference | Reference (`incident`) | Valid Incident `sys_id` | Foreign key binding the audit record to the parent incident. |
| `execution_id` | Execution ID | String (40) | Unique string (e.g. `exec_verify_<hex>`) | **Indexed unique trace key** for distributed tracing and single-record lookup. |
| `agent` | Agent | String (100) | Free text (e.g. `verification_harness`, `triage_agent`) | Attributes work to the exact agent / workflow node. |
| `action` | Action | String (100) | `read`, `execute`, `propose`, `escalate` | The specific operational action attempted. |
| `status` | Status | String (40) | `succeeded`, `failed`, `blocked`, `awaiting_approval` | Durable execution lifecycle outcome. |
| `timestamp` | Timestamp | Date/Time | UTC format (`YYYY-MM-DD HH:MM:SS`) | Timestamp of execution start/event. |
| `result` | Result | String (4000) | Max 4000 characters | Diagnostic summary, classification output, or resolution suggestion. |
| `error` | Error | String (4000) | Max 4000 characters (blank on success) | Full diagnostic error message, stack trace, or reason for blockage. |

#### Append-Only Protection (SS7 / LOG-05)
To prevent rogue actors or automation bugs from tampering with audit records, the table enforces an **append-only policy**:
- **Delete ACL Rule**: `x_2215032_ai_inc_0_ai_execution_log` (operation: `delete`).
- **Policy**: `Deny Unless` role `admin` (or delete role strictly restricted from `integration_writer` and `ai_execution_log_user`).
- **Observed Behavior**: When `ai_orchestrator_svc` executes `DELETE /api/now/table/x_2215032_ai_inc_0_ai_execution_log/{sys_id}`, ServiceNow responds with `HTTP 403 Forbidden` and preserves the record unaltered.

---

### 4. Least-Privilege Access Control List (ACL) Matrix

Permissions are bounded strictly to the minimal operational surface required for AI triage and logging:

```
+--------------------------------------------------------------------------------+
| ServiceNow Incident Table Security Perimeter                                   |
|                                                                                |
|  [PERMITTED WRITES]                                                            |
|    * incident.work_notes                                -> Internal ITIL notes |
|    * incident.x_2215032_ai_inc_0_ai_classification      -> Agent Taxonomy Tag  |
|                                                                                |
|  [FORBIDDEN WRITES - HARD ACL BLOCKED]                                          |
|    * incident.state             -> Prevents unauthorized incident closure      |
|    * incident.assigned_to       -> Prevents re-assignment loops                |
|    * incident.assignment_group  -> Prevents routing hijack                     |
|    * incident.priority          -> Prevents unauthorized P1 escalations        |
|    * incident.comments          -> Prevents unreviewed customer-facing leaks   |
|    * incident.ai_human_lock     -> Circuit breaker cannot be disabled by AI    |
+--------------------------------------------------------------------------------+
```

#### Field Permission Rationale

| Field Target | Permission | Intended Role | Rationale |
|---|:---:|---|---|
| `incident.work_notes` | **ALLOW** | `integration_writer` | Internal journal field. Allows the orchestrator to publish diagnostic findings, runbooks, and suggested steps visible only to ITIL fulfillers. |
| `incident.x_2215032_ai_inc_0_ai_classification` | **ALLOW** | `integration_writer` | Scoped field authored by the LangGraph Classification Agent to record incident category (`software`, `hardware`, etc.). |
| `incident.state` | **DENY** | Human Fulfiller / Admin | Prevents autonomous agents from resolving or closing incidents without human approval. |
| `incident.assigned_to` | **DENY** | Human Fulfiller / Dispatcher | Prevents automated assignment loops or uncoordinated reassignment. |
| `incident.assignment_group` | **DENY** | Service Desk Lead / Admin | Prevents ticket hijacking across support departments. |
| `incident.priority` | **DENY** | ITIL Fulfiller / Incident Commander | Prevents false-alarm P1 escalations that trigger organization-wide alerts. |
| `incident.comments` | **DENY** | Fulfiller / Caller | Customer-facing journal. Protects end users from raw, unverified AI outputs. |
| `incident.x_2215032_ai_inc_0_ai_human_lock` | **DENY** | Human Fulfiller Only | **Emergency Circuit Breaker**. If set to `true`, all automated runs halt. The AI must never be able to unlock itself. |

---

### 5. Empirical Verification Matrix (100% PASS)

The test harness [`scripts/verify_permissions.py`](../../../scripts/verify_permissions.py) was executed against live ServiceNow instance `dev407364.service-now.com` using target incident `INC0010003` (opened by a third party to prevent creator-privilege bias). Every test performs real HTTP transactions with read-after-write database queries.

#### Complete Test Run Results (21 of 21 Passed)

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
| **LOG-01**  | Execution Log | `POST x_..._ai_execution_log` (`succeeded`) | Status `succeeded` audit record created | HTTP 201 Created | **PASS** |
| **LOG-02**  | Execution Log | `POST x_..._ai_execution_log` (`failed`) | Status `failed` audit record created | HTTP 201 Created | **PASS** |
| **LOG-03**  | Execution Log | `POST x_..._ai_execution_log` (`blocked`) | Status `blocked` audit record created | HTTP 201 Created | **PASS** |
| **LOG-04**  | Execution Log | `GET x_..._ai_execution_log?execution_id=` | Indexed query returns exactly 1 record | HTTP 200 (1 record returned) | **PASS** |
| **LOG-05**  | Execution Log | `DELETE x_..._ai_execution_log/{id}` | Append-only: Delete blocked with 403 | HTTP 403 (Record exists) | **PASS** |
| **DENY-01** | Forbidden | `PATCH incident.state` | State modification rejected | HTTP 200 (State unchanged `1`) | **PASS** |
| **DENY-02** | Forbidden | `PATCH incident.assigned_to` | Assignment modification rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-03** | Forbidden | `PATCH incident.assignment_group` | Group reassignment rejected | HTTP 200 (Value unchanged) | **PASS** |
| **DENY-04** | Forbidden | `PATCH incident.priority` | Priority escalation rejected | HTTP 200 (Priority unchanged `5`)| **PASS** |
| **DENY-05** | Forbidden | `PATCH incident.comments` | Customer comments stripped from journal | HTTP 200 (0 journal entries) | **PASS** |
| **LOCK-01** | Human Lock | `PATCH incident.ai_human_lock` | Circuit breaker modification rejected | HTTP 200 (Lock unchanged `false`)| **PASS** |
| **BULK-01** | Bulk Bypass | `PATCH incident` (Mixed payload) | Permitted written, all forbidden stripped | `work_notes` wrote; rest blocked | **PASS** |
| **CRED-01** | Cleanliness | Repository Secret Scan | Zero secrets/passwords in tracked files | 60 files scanned clean | **PASS** |

> [!NOTE]
> **ServiceNow Field-Level Stripping Semantics**: When a client sends a `PATCH` request containing forbidden fields, ServiceNow returns `HTTP 200 OK` while silently stripping unauthorized fields in accordance with ACL rules. The verification harness never relies on HTTP status codes alone; every test performs an independent read-after-write GET request against the database and `sys_journal_field` to prove that forbidden values were never persisted.

---

### 6. Repository Credential Cleanliness (FR-07 / CRED-01)

All credentials and sensitive configuration adhere to strict hygiene:
- Credentials reside solely in local, git-ignored `.env` files.
- Tracked configuration (`src/app/core/config.py`) defines settings via Pydantic `SecretStr` models with zero hardcoded credentials or defaults.
- The automated repository scanner (`CRED-01`) recursively analyzes all tracked source, markdown, YAML, JSON, and TOML files against high-entropy regex patterns, verifying zero committed passwords, API keys, client secrets, or Basic auth tokens across all 60 repository files.

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
