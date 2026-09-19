# ServiceNow outbound webhook authentication

The incident webhook uses OAuth 2.0 client credentials. ServiceNow requests a
short-lived access token and presents that token to
`POST /api/v1/webhook/incident`. Static bearer tokens are not accepted by the
webhook.

This is the same contract installed by the S1.3 outbound OAuth update set. See
[`../../sprint-1/s1.3-eligibility-rule-and-event/outbound-oauth.md`](../../sprint-1/s1.3-eligibility-rule-and-event/outbound-oauth.md)
for the exported ServiceNow record IDs and clean-install evidence.

## Backend configuration

Set four independent values. None has a repository default:

```dotenv
# Operator-facing routes such as approvals, config, DLQ and evaluation.
# ServiceNow does not receive this value.
WEBHOOK_AUTH_TOKEN=

# ServiceNow's outbound OAuth client.
WEBHOOK_OAUTH_CLIENT_ID=barq-servicenow
WEBHOOK_OAUTH_CLIENT_SECRET=
WEBHOOK_OAUTH_SIGNING_KEY=
```

`WEBHOOK_OAUTH_SIGNING_KEY` must contain at least 32 characters. Generate the
client secret and signing key independently. The application refuses to start
when any required value is absent; Docker Compose uses the same fail-closed
requirements.

The backend exposes:

- `POST /api/v1/oauth/token` — accepts `client_credentials` in the form body or
  HTTP Basic authentication and returns a five-minute HS256 JWT;
- `POST /api/v1/webhook/incident` — accepts only a valid, unexpired JWT issued by
  that endpoint;
- operator-facing API routes — accept only `WEBHOOK_AUTH_TOKEN`, never the
  ServiceNow OAuth token.

This separation prevents the ServiceNow integration identity from using human
approval, configuration, DLQ, execution-query or evaluation endpoints.

## ServiceNow configuration

1. Import the S1.3 outbound OAuth update set.
2. Open OAuth provider `BARQ Webhook OAuth` and set:
   - Token URL: `{backend}/api/v1/oauth/token`
   - Client ID: the backend's `WEBHOOK_OAUTH_CLIENT_ID`
   - Client secret: the backend's `WEBHOOK_OAUTH_CLIENT_SECRET`
3. Confirm REST Message `AI Incident Orchestrator S1.3 Event` uses OAuth 2.0 and
   profile `BARQ Webhook OAuth default_profile`.
4. Set property `x_2215032_ai_inc_0.s1_3_event_endpoint` to
   `{backend}/api/v1/webhook/incident`.

Do not add a static `Authorization` header to the REST Message. ServiceNow
obtains and refreshes the access token through the OAuth profile.

## Verification

The automated suite proves the production routers are wired together:

- valid client credentials → token endpoint `200` → webhook `202`;
- missing, malformed, expired, forged or static bearer token → webhook `401`;
- ServiceNow OAuth token → operator route `401`;
- missing backend auth configuration → settings validation failure.

For a live proof, create one eligible AI-enabled incident and confirm, in order:

1. ServiceNow obtains a token successfully;
2. the webhook returns `202`;
3. an `events` row and one idempotency/execution chain are created;
4. Celery reaches a terminal graph outcome;
5. the incident receives the expected AI fields and work note.
