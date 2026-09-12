# PR #14 Summary: S1.2 ServiceNow Security & Audit Foundation (Rework & Final Verification)

### 📌 Overview
This PR completes the rework requested during the Tech Lead and Peer reviews for **Task S1.2**. All reported defects, documentation drift, scoping errors, and security recommendations have been resolved and verified with empirical test evidence on live PDI (`dev407364`).

---

### 🛠️ Key Changes & Resolutions

#### 1. Execution Log Choice Taxonomy Alignment (Aya / Tech Lead Approved)
* **`status` Choices**:
  * Corrected internal value from display string `'Awaiting approval'` &rarr; snake_case **`awaiting_approval`**.
  * Added new choice **`abandoned`** to support FR-02 requirements.
  * Complete durable choice list: `started`, `succeeded`, `failed`, `blocked`, `awaiting_approval`, `abandoned`.
* **`action` Choices**: Capped at 40 chars; standardized across platform and Python enums: `read`, `execute`, `propose`, `escalate`.

#### 2. Access Control List (ACL) Corrections
* **`ai_human_review_required` Write ACL**:
  * Created scoped record `sys_security_acl` for `incident.x_2215032_ai_inc_0_ai_human_review_required`.
  * Linked role `x_2215032_ai_inc_0.integration_writer` to grant required triage write permissions (`PERM-04`).
* **Table-Level Append-Only Delete ACL (`LOG-05`)**:
  * Mapped table delete ACL `x_2215032_ai_inc_0_ai_execution_log` strictly to `admin`.
  * Removed erroneous `itil` role mapping and invalid field-level delete ACLs.
  * Verified that attempts by `ai_orchestrator_svc` to delete records return **`HTTP 403 Forbidden`**.

#### 3. Platform-Side Human Lock Safety Stop (Business Rule S2.7)
* Addressed the REST Table API race condition where client-side checks can be bypassed by concurrent operator updates.
* Implemented `before-update` Business Rule **`AI Enforce Human Lock Safety Stop`** (Order `50`) on the `incident` table inside scope `x_2215032_ai_inc_0`.
* Enforces server-side termination (`current.setAbortAction(true)`) and returns an explicit rejection if `ai_human_lock == true` when modified by the AI integration identity.

#### 4. Credential Rotation & Cleanliness (DoD Compliance)
* **OAuth Secret**: Regenerated live client secret in ServiceNow Application Registry.
* **Service Account**: Password for `ai_orchestrator_svc` rotated to a high-entropy credential.
* **Repository Secret Scan (`CRED-01`)**: 93 files scanned clean (0 hardcoded credentials in repo).

#### 5. Documentation Reconciliation (`audit-and-identity.md`)
* Reconciled all documentation against the source XML.
* Updated `execution_id` length specification to `String(100)`.
* Fully documented all 12 permitted write fields, append-only RBAC mechanics, and the layered defense-in-depth architecture.

---

### 🧪 Empirical Verification Evidence (25 / 25 PASS)

Executed via automated test harness `scripts/verify_permissions.py` against `dev407364.service-now.com`:

```text
========================================================================
  Sprint 1 (S1.2) Security Verification - Final Report
========================================================================

  -- Authentication --
  [PASS] +  AUTH-01: OAuth token acquisition (Lifespan: 1799s)
  [PASS] +  AUTH-02: Identity is expected service account (ai_orchestrator_svc)
  [PASS] +  AUTH-03: Non-admin: sys_user_has_role access denied (no security_admin)
  [PASS] +  AUTH-04: Invalid/expired token is rejected (HTTP 401)

  -- Token Lifecycle --
  [PASS] +  TOKEN-01: Mid-run expiry detected (harness handles 401 re-auth)

  -- Permitted Operations --
  [PASS] +  PERM-01: Read incident INC0010003
  [PASS] +  PERM-02: Write work_notes (internal journal verified, count=1)
  [PASS] +  PERM-03: Write scoped AI field (x_2215032_ai_inc_0_ai_classification)
  [PASS] +  PERM-04: Write scoped AI field (x_2215032_ai_inc_0_ai_human_review_required)

  -- Execution Log (FR-02) --
  [PASS] +  LOG-01: Create execution log: status=succeeded
  [PASS] +  LOG-02: Create execution log: status=failed
  [PASS] +  LOG-03: Create execution log: status=blocked
  [PASS] +  LOG-06: Create execution log: status=abandoned
  [PASS] +  LOG-04: Execution ID indexed lookup (unique btree key)
  [PASS] +  LOG-05: Cannot delete execution log records (HTTP 403 Forbidden; append-only)

  -- Forbidden Incident Fields --
  [PASS] +  DENY-01: FORBIDDEN write to incident.state (ACL blocked)
  [PASS] +  DENY-02: FORBIDDEN write to incident.assigned_to (ACL blocked)
  [PASS] +  DENY-03: FORBIDDEN write to incident.assignment_group (ACL blocked)
  [PASS] +  DENY-04: FORBIDDEN write to incident.priority (ACL blocked)
  [PASS] +  DENY-05: FORBIDDEN write to incident.comments (0 journal entries - ACL blocked)

  -- Human Lock & Circuit Breakers --
  [PASS] +  LOCK-01: Integration CANNOT modify human-lock (circuit breaker tamper-proof)
  [PASS] +  LOCK-02: Integration CANNOT modify AI-enabled (opt-in switch tamper-proof)
  [PASS] +  LOCK-03: Platform Business Rule enforces safety stop on locked incident

  -- Bulk Bypass --
  [PASS] +  BULK-01: Mixed payload: permitted persists, forbidden fields stripped

  -- Credential Cleanliness --
  [PASS] +  CRED-01: Repository secret scan (Clean - 93 files scanned)

========================================================================
  RESULT: PASS  (25/25 tests)
========================================================================
```

---

### ✅ Definition of Done (DoD) Checklist
- [x] Scoped application discipline maintained (`x_2215032_ai_inc_0`).
- [x] Execution log table enforces append-only policy via platform ACLs (verified with HTTP 403).
- [x] Dedicated machine service account with least-privilege roles only (0 admin / 0 security_admin).
- [x] Machine-readable verification output generated (`verification_report.json`).
- [x] Zero hardcoded credentials committed anywhere in repository.
