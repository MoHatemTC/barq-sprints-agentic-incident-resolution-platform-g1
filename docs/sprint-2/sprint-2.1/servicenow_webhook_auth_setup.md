# ServiceNow Outbound Webhook Authentication Setup (Sprint 2)

## Overview
Per **FR-08** and the Sprint 2 deliverables specification, all inbound webhook traffic to the platform (`POST /api/v1/webhook/incident`) must enforce caller authentication with an HTTP `401 Unauthorized` response on missing or invalid credentials.

In Sprint 1, outbound calls from ServiceNow used `No Authentication` because they targeted a temporary inspection endpoint (`webhook.site`). In Sprint 2, ServiceNow attaches a shared secret Bearer token to all outbound incident event dispatches.

---

## Configuration in ServiceNow PDI

### Method 1: REST Message Header Configuration (Recommended)
This approach configures the static Bearer token directly on the ServiceNow Outbound REST Message without modifying scripts:

1. In the ServiceNow Filter Navigator, navigate to:
   `System Web Services` ➔ `Outbound` ➔ `REST Message`
2. Open the record:
   **`AI Incident Orchestrator S1.3 Event`**
3. In the **HTTP Methods** related list at the bottom, click into the **`post`** method.
4. **Authentication Tab**:
   - Set / Keep **Authentication type**: `No authentication`
   - *Rationale*: Setting this to `Basic` causes ServiceNow to auto-inject an `Authorization: Basic ...` header. Keeping it as `No authentication` allows our custom header to pass through cleanly without interference.
5. **HTTP Request ➔ HTTP Headers Section**:
   - Click **New** (or insert row):
     - **Name**: `Authorization`
     - **Value**: `Bearer <WEBHOOK_AUTH_TOKEN>` (e.g. `Bearer barq-webhook-secret-token-123`)
6. Click **Update** to save changes.

---

### Method 2: Dynamic System Property via Script Action
If dynamic configuration via ServiceNow System Properties is preferred:

1. **Create System Property**:
   - Navigate to `sys_properties.list` in the filter navigator.
   - Click **New**:
     - **Name**: `x_2215032_ai_inc_0.webhook_token`
     - **Type**: `string`
     - **Value**: `<your-pre-shared-secret-token>`
   - Save the record.

2. **Update Script Action**:
   - Navigate to `System Policy` ➔ `Events` ➔ `Script Actions`.
   - Open **`AI Incident Orchestrator - Send S1.3 Event`**.
   - Right after `request.setEndpoint(endpoint);`, attach the header:
     ```javascript
     var token = String(gs.getProperty('x_2215032_ai_inc_0.webhook_token', '') || '').trim();
     if (token) {
         request.setRequestHeader('Authorization', 'Bearer ' + token);
     }
     ```
   - Save the script action.

---

## Backend Alignment (FastAPI)

1. **Configuration**:
   - The same secret token is configured in `.env`:
     ```bash
     WEBHOOK_AUTH_TOKEN="<your-pre-shared-secret-token>"
     ```
   - Loaded in `src/app/core/config.py` as a `SecretStr`.

2. **Endpoint Enforcement (`src/api/routers/webhook.py`)**:
   - Extracts `Authorization: Bearer <token>`.
   - Performs constant-time comparison against `settings.webhook_auth_token.get_secret_value()`.
   - If missing or mismatched ➔ Raises `AuthenticationError` returning:
     ```json
     {
       "error": {
         "code": "AUTHENTICATION_FAILED",
         "message": "Invalid or missing Bearer token",
         "details": {},
         "correlation_id": "...",
         "timestamp": "..."
       }
     }
     ```

---

## Test Verification
Automated test coverage in `tests/test_webhook.py` validates both authentication paths:
- Missing `Authorization` header ➔ **401 Unauthorized**
- Malformed or invalid Bearer token ➔ **401 Unauthorized**
- Valid Bearer token matching `settings.webhook_auth_token` ➔ **202 Accepted**
