# S1.5 | ServiceNow Table API Client, Incident Read/Write & Execution Log

**Owner:** [@Tasneemmohammed0](https://github.com/Tasneemmohammed0)
**Tracking:** [#27](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/27)

## Delivered

### OAuth Token Handling (FR-06)

Added `ServiceNowTokenManager` to handle access token caching, expiry, and refresh.

* Tokens remain valid until they reach the configured expiry buffer, `servicenow_token_expiry_buffer_seconds`.
* The initial token is obtained using the password grant. Subsequent refreshes use the stored refresh token.
* If a request receives a `401`, `ServiceNowClient` refreshes the token and retries the request once.
* Token acquisition and refreshes are protected by an `asyncio.Lock` to prevent duplicate refreshes when multiple requests encounter an expired token at the same time.
* A failed refresh clears the in-memory token state and re-raises the error.
* Credentials are never included in logs or exception messages.

### Incident Reading

Added support for:

* `get_incident(sys_id)` to fetch an incident directly by `sys_id`.
* `find_incident_by_number(number)` to find an incident by its incident number.
* `find_incident_by_number` returns `None` when no matching incident is found.
* If ServiceNow returns multiple rows for the same incident number, a `ServiceNowError` is raised instead of silently selecting one.

### Incident Writing

Added support for updating AI-related incident fields through:

* `update_incident(sys_id, payload)`
* `add_work_note(sys_id, note)`

`IncidentUpdatePayload` validates the payload before sending it to ServiceNow. The validation includes:

* A `failed` state requires a non-blank `ai_failure_reason`.
* A `complete` state requires both `ai_processing_end` and `ai_resolution`.
* `ai_processing_end` cannot be earlier than `ai_processing_start`.

### AI Execution Logging (FR-02)

Added `write_execution_log(payload)` to record every processing attempt in the `ai_execution_log` table.

Supported execution states are:

* `started`
* `succeeded`
* `failed`
* `blocked`
* `awaiting_approval`
* `abandoned`

Execution log writes are intentionally non-blocking for the main pipeline. If the audit table write fails, the error is logged and the method returns `None` instead of bringing down incident processing.

### Error Handling

Added a typed ServiceNow exception hierarchy based on `ServiceNowError`:

* `ServiceNowAuthenticationError` for 401 responses
* `ServiceNowAuthorizationError` for 403 responses
* `ServiceNowNotFoundError` for 404 responses
* `ServiceNowRateLimitError` for 429 responses, including `retry_after`
* `ServiceNowValidationError` for 400 and 422 responses
* `ServiceNowServerError` for 5xx responses
* `ServiceNowTimeoutError` for request timeouts
* `ServiceNowConnectionError` for connection failures

Response bodies are truncated to 500 characters before being included in exceptions.

### Response Sanitisation

`Incident` normalises ServiceNow's empty string values for unset fields.

Downstream code therefore receives:

* `False` for unset boolean fields
* `pending` for an unset `ai_processing_state`
* `None` for other unset fields

This keeps ServiceNow-specific response quirks out of the rest of the application.

## Requirements Covered

* **FR-02:** Every processing attempt is logged, including successful, failed, blocked, and approval-waiting attempts. The execution log acts as an audit trail, while logging failures do not bring down the processing pipeline.
* **FR-06:** Token expiry is handled during long-running graph executions, including refreshing and retrying when a token expires in the middle of a request.
