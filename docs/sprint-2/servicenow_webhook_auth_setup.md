# ServiceNow Outbound Webhook Authentication Setup (Sprint 2)

> **This file is a pointer, not the setup.** It used to describe a static
> `Authorization: Bearer <WEBHOOK_AUTH_TOKEN>` header on the REST message, which
> ServiceNow cannot deliver: the S1.3 REST message authenticates with **OAuth 2.0
> client credentials** since #125/#139, and the backend answers `401` to the static
> token (`AUTHENTICATION_FAILED`). Following the old instructions produces events
> that never reach the webhook (#137).

**The setup lives in
[`sprint-2.1/servicenow_webhook_auth_setup.md`](sprint-2.1/servicenow_webhook_auth_setup.md)**
— the same OAuth flow S1.3 documents in
[`../sprint-1/s1.3-eligibility-rule-and-event/outbound-oauth.md`](../sprint-1/s1.3-eligibility-rule-and-event/outbound-oauth.md):

1. `oauth_entity` **BARQ Webhook OAuth** → Token URL `{backend}/api/v1/oauth/token`,
   Client Secret = the backend's `WEBHOOK_OAUTH_CLIENT_SECRET`.
2. REST message **AI Incident Orchestrator S1.3 Event** → Authentication `oauth2`,
   profile `BARQ Webhook OAuth default_profile`.
3. Property `x_2215032_ai_inc_0.s1_3_event_endpoint` → `{backend}/api/v1/webhook/incident`.

ServiceNow fetches a token per call and sends `Authorization: Bearer <JWT>`; the
backend verifies it with `verify_webhook_oauth_token` (`src/api/auth.py`).

## Payload

The script action sends exactly the S1.3 four-field payload
(`event_id`, `sys_id`, `number`, `event_type`). `contract_version` is **not** sent;
the webhook defaults it to `"v1"` (#137), so no ServiceNow-side change is required.
Unsupported versions are still rejected with `422 UNKNOWN_CONTRACT_VERSION`.

## Static token: local testing only

`WEBHOOK_AUTH_TOKEN` remains in the configuration for local development — pointing a
hand-rolled client at the webhook without an OAuth round trip. It is **not** the
ServiceNow setup: the production webhook path verifies the OAuth JWT, and a static
value in the `Authorization` header is refused. Do not document or demo it as the
integration path.
