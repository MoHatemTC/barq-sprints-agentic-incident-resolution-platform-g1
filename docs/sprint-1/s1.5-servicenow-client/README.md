# S1.5 — ServiceNow Table API client, incident read/write & execution log

**Owner:** [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) · **Tracking:** [#27](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/27)


### Delivered Capabilities

- **OAuth Token Handling (FR-06):** `ServiceNowTokenManager` proactively caches the access token and treats it as valid until it falls inside a configurable expiry buffer (`servicenow_token_expiry_buffer_seconds`). First acquisition uses the password grant; subsequent refreshes use the stored refresh token. On a `401` mid-request, `ServiceNowClient._request` forces a refresh and retries exactly once before surfacing an error — so a token that expires partway through a long-running graph execution is transparently recovered rather than bubbling up as an authorisation failure at the write step. Refresh/fetch calls are serialised behind an `asyncio.Lock`, with a `failed_token` check so concurrent callers racing on the same expired token don't trigger duplicate refreshes. A failed refresh purges all in-memory token state and re-raises. Credentials and raw response bodies are deliberately excluded from every log line and exception message.
- **Incident Reading:** `get_incident(sys_id)` fetches by `sys_id`; `find_incident_by_number(number)` looks up by incident number, returns `None` on no match, and raises `ServiceNowError` if ServiceNow ever returns more than one row for the same number.
- **Incident Writing:** `update_incident(sys_id, payload)` PATCHes the scoped AI fields (classification, confidence, resolution, model/agent version, processing timestamps, human-review flags, failure reason); `add_work_note(sys_id, note)` appends to the incident's work-notes journal. `IncidentUpdatePayload` enforces the write-time contract before anything hits the wire: a `failed` state requires a non-blank `ai_failure_reason`, a `complete` state requires both `ai_processing_end` and a non-blank `ai_resolution`, and `ai_processing_end` can never precede `ai_processing_start`.
- **AI Execution Logging (FR-02):** `create_execution_log(payload)` POSTs a record — `succeeded`, `failed`, `blocked`, or `awaiting_approval` — to the scoped `ai_execution_log` table for every processing attempt. It's deliberately safe-failure: a write exception is caught, logged, and the method returns `None` instead of propagating, so an outage in the audit table can never take down the incident-processing pipeline it exists to audit.
- **Robust Error Mapping:** `_parse_response` maps ServiceNow HTTP responses onto a typed exception hierarchy off a common `ServiceNowError` base (`status_code` + `details`): `ServiceNowAuthenticationError` (401), `ServiceNowAuthorizationError` (403), `ServiceNowNotFoundError` (404), `ServiceNowRateLimitError` (429, carries `retry_after`), `ServiceNowValidationError` (400/422), `ServiceNowServerError` (5xx), plus `ServiceNowTimeoutError` / `ServiceNowConnectionError` for transport-level failures. Response bodies are truncated to 500 characters before being attached to any exception.
- **Response Sanitisation:** `Incident` normalises ServiceNow's habit of returning empty strings for unset fields on read — booleans fall back to `False`, `ai_processing_state` falls back to `pending`, everything else falls back to `None` — so downstream code never has to special-case `""`.

## What lands in this folder once merged

- `ServiceNowTokenManager` and `ServiceNowClient` (auth + client layer)
- The `ServiceNowError` exception hierarchy
- Pydantic models: `Incident`, `IncidentUpdatePayload`, `ExecutionLogEntry` / `ExecutionLogCreatePayload`, `OAuthTokenResponse`, `WorkNoteUpdate`
- Unit tests: `tests/auth/test_token_manager.py` (acquisition, refresh, expiry buffer, invalidate, network errors, credential safety) and `tests/clients/test_servicenow_client.py`
- `scripts/test_client.py` — a manual end-to-end script exercising all six client operations against a live PDI instance

## Requirements

FR-02 (a log record for every attempt, including blocked, failed and
abandoned ones — a client that only logs successes is worse than no log at
all) and the mid-run token expiry handling FR-06 implies for a long-running
graph execution.