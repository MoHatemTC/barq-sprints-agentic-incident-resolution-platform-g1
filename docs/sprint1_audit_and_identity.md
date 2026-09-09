# Sprint 1 (Task S1.2): ServiceNow Security & Audit Foundation
## Identity, Least-Privilege Access Control, and OAuth Lifecycle

### 1. Overview & Components
This deliverable establishes the platform's audit trail, non-admin integration identity, and least-privilege access control rules within ServiceNow (Task S1.2).

- **Service Account**: `ai_orchestrator_svc` (Machine account, non-admin, Web Service Access Only)
- **Assigned Role**: `u_ai_service_role` (Contains `snc_platform_rest_api_access`, `u_ai_execution_log_user`)
- **OAuth Application**: `AI Incident Orchestrator External Client` (Password grant type)
- **Verification Harness**: [`scripts/verify_permissions.py`](../scripts/verify_permissions.py)
- **ServiceNow Update Set**: [`servicenow/ai_incident_orchestrator/sys_remote_update_set_882770ec47570310c148497f316d4329.xml`](../servicenow/ai_incident_orchestrator/sys_remote_update_set_882770ec47570310c148497f316d4329.xml)

---

### 2. Custom Table: AI Execution Log (`u_ai_execution_log`)
To satisfy **FR-02** (audit trail of all AI actions), a custom table was created in ServiceNow to capture every processing attempt:

| Column Name | Label | Type | Description |
| :--- | :--- | :--- | :--- |
| `u_incident` | Incident | Reference (`incident`) | Links log entry to the parent incident |
| `u_execution_id` | Execution ID | String (40) | Unique trace identifier (**indexed for fast lookup**) |
| `u_action` | Action | String (100) | Operation performed (e.g., triage, summarize, diagnose) |
| `u_agent` | Agent | String (100) | Identifier of the invoking agent / workflow node |
| `u_timestamp` | Timestamp | Date/Time | Execution timestamp |
| `u_status` | Status | String (40) | Execution outcome (`SUCCESS`, `FAILED`, `BLOCKED`) |
| `u_result` | Result | String (4000) | Formatted result payload or summary |
| `u_error` | Error | String (4000) | Detailed error message and stack trace if failed |

- **Related List**: Configured on the Incident form layout to display attempt history directly to ITIL technicians.

---

### 3. Verified Permission Matrix (Empirical Evidence)

Executed against live instance (`dev407364.service-now.com`) on incident `INC0000060` using `scripts/verify_permissions.py`:

| Test ID | Operation | Target Resource / Field | Expected Outcome | Observed HTTP | Database State Verification | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **TEST-01** | `CREATE` | `u_ai_execution_log` | 201 Created | HTTP 201 Created | Record created with generated sys_id | **PASS** |
| **TEST-02** | `WRITE` | `incident.work_notes` | 200 OK | HTTP 200 OK | Work note successfully appended to journal | **PASS** |
| **TEST-03** | `WRITE` | `incident.state` | Denied | HTTP 200 (field stripped) | State unchanged (`7` closed, update rejected) | **PASS** |
| **TEST-04** | `WRITE` | `incident.assigned_to` | Denied | HTTP 200 (field stripped) | Assignment unchanged (reassignment rejected) | **PASS** |
| **TEST-05** | `WRITE` | `incident.priority` | Denied | HTTP 200 (field stripped) | Priority unchanged (`3` moderate, P1 escalation rejected) | **PASS** |
| **TEST-06** | `WRITE` | `incident.comments` | Denied | HTTP 200 (field stripped) | Customer comment stripped; not posted | **PASS** |
| **TEST-07** | `WRITE` | `incident.u_human_lock` | Denied | HTTP 200 (field stripped) | Lock flag unmodified (`true` preserved) | **PASS** |

> **Result**: 7 / 7 tests passed (100% compliance).
>
> **ServiceNow Field-Level ACL Behavior**: When updating an incident via `PATCH /api/now/table/incident/{sys_id}`, ServiceNow evaluates field-level ACLs. Unauthorized fields are silently stripped from the update payload while permitted fields are written, returning HTTP 200. The test harness verifies enforcement by querying the database post-update to confirm that restricted values remained completely unaltered.

---

### 4. Least-Privilege Access Control Rationale

Permissions are bounded strictly to the minimal operational surface needed:

#### Permitted Boundaries
1. **`u_ai_execution_log` (CREATE & READ)**:
   - *Requirement*: FR-02 (Audit Trail).
   - *Rationale*: Allows the platform to record an append-only trace of every agent attempt, diagnosis, and failure, and query execution history for contextual triage.
2. **`incident.work_notes` (WRITE)**:
   - *Requirement*: FR-05 (Internal Operator Visibility).
   - *Rationale*: Allows agents to post diagnostic findings, runbook references, and recommended steps. Work notes are internal to ITIL operators and are never transmitted to end users or customers.

#### Denied Boundaries
1. **`incident.state`**: Prevents an autonomous system from prematurely resolving or closing incidents without human validation.
2. **`incident.assigned_to` / `assignment_group`**: Prevents automated ticket re-routing loops and unauthorized reassignments.
3. **`incident.priority`**: Prevents runaway escalation to Critical / P1, which triggers company-wide incident command alerts.
4. **`incident.comments` (Customer-Facing)**: Prevents raw AI output from being directly emailed to customers; customer communications require human review.
5. **`incident.u_human_lock`**: Protects the circuit breaker so the AI cannot disable its own safety lock.

---

### 5. OAuth 2.0 Token Lifecycle

#### Authentication & Refresh Flow
```
1. Client Token Request:
   POST /oauth_token.do
   Headers: Content-Type: application/x-www-form-urlencoded
   Body:    grant_type=password&client_id=<ID>&client_secret=<SECRET>&username=ai_orchestrator_svc&password=<PWD>

2. Instance Response:
   HTTP 200 OK
   {
     "access_token": "...",
     "refresh_token": "...",
     "expires_in": 1800,
     "token_type": "Bearer"
   }

3. Subsequent API Calls:
   Headers: Authorization: Bearer <access_token>

4. Token Refresh:
   POST /oauth_token.do
   Body:    grant_type=refresh_token&client_id=<ID>&client_secret=<SECRET>&refresh_token=<REFRESH_TOKEN>
```

#### Mid-Run Expiry Handling
- **Pre-Flight Threshold (Proactive)**: If token expiration has less than 120 seconds remaining before starting a multi-step agent execution, the client requests a refreshed token in advance.
- **HTTP 401 Re-Authentication (Reactive)**: If an API call receives an HTTP 401 response due to clock drift or token invalidation, the HTTP client invalidates the cached token, requests a new token via refresh token, and replays the original request once before failing.
