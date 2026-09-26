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
# Operator client's secret, exchanged at /api/v1/oauth/token for an operator
# JWT. ServiceNow does not receive this value, and it is never accepted as a
# bearer token itself.
WEBHOOK_AUTH_TOKEN=

# Subject of every operator token, and the roles its claim carries.
OPERATOR_CLIENT_ID=barq-operator
OPERATOR_ROLES=["operator","approver"]

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
- operator-facing API routes — accept only an operator JWT, issued to the
  operator client (`OPERATOR_CLIENT_ID`) in exchange for `WEBHOOK_AUTH_TOKEN`,
  with its `roles` claim from `OPERATOR_ROLES`.

`POST /api/v1/oauth/token` therefore serves two clients with two audiences: the
ServiceNow client gets the webhook token, the operator client gets the operator
token. A token is refused wherever its audience does not match, so the webhook
JWT is `401` on `/approvals`, `/config`, `/dlq`, `/executions` and `/eval`, and
the operator JWT is `401` on the incident webhook. The raw `WEBHOOK_AUTH_TOKEN`
is never accepted as a bearer token, so nothing ServiceNow can present reaches
the human approval, configuration, DLQ, execution-query or evaluation
endpoints.

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

## Live proof — 2026-09-26

Run by `ali-ezz` on PDI **dev434590** against the deployed backend host, steps 1–5
in the order above.

| | |
|---|---|
| Instance | `dev434590` |
| Incident | `INC0010028`, sys_id `ba0081f1736b83902aedfed25ab8b7c5`, created `2026-09-26 04:01:14 UTC` (07:01:14 Cairo) |
| Eligibility event | `x_2215032_ai_inc_0.s1_3_outbound_event`, queued `04:01:15`, processed `04:01:25`, sys_id `030081f1736b83902aedfed25ab8b7c8` |
| Emitted event | `event_id` `cb0081f1736b83902aedfed25ab8b7c7`, `event_type` `incident.created` |

**Outbound HTTP log** (`sys_outbound_http_log`, the two records the run produced):

| Time (UTC) | Request | Result | Record |
|---|---|---|---|
| 2026-09-26 04:01:25 | `POST http://51.21.182.56:8000/api/v1/oauth/token` | **200** | `611005f1736b83902aedfed25ab8b742` |
| 2026-09-26 04:01:25 | `POST http://51.21.182.56:8000/api/v1/webhook/incident` | **202** | `a51005f1736b83902aedfed25ab8b743` |

**Backend log**, same two calls (`User-Agent: ServiceNow/1.0`, caller
`148.139.124.20`): `/api/v1/oauth/token` `200`, correlation
`a9db447b-15d3-4afd-bd72-d92d8c565459`; `/api/v1/webhook/incident` `202`,
correlation `7338ec1d-107a-42e9-a66e-86c7d9de4c04`.

**Database:**

- `events` — `cb0081f1736b83902aedfed25ab8b7c7`, `INC0010028`,
  `contract_version` **`v1`** (the default: ServiceNow sends no version field),
  received `2026-09-26 04:01:25.701688+00`.
- `executions` — `bc13ca87-f6aa-41e8-94eb-f780087bb4b8`, started
  `2026-09-26 04:01:25.701688+00`, one execution for that event.

Steps 1–3 pass. Step 4 resolves to a terminal outcome: the worker recorded
`terminal failure: ServiceNow refused the request: ServiceNowNotFoundError` and
dead-lettered the event, because the backend's `SERVICENOW_INSTANCE_URL` points at
`dev407364` while this incident lives on `dev434590`, so the graph's read-back of the
incident 404s. That mismatch is downstream of ingestion — it is an environment
configuration, not an authentication or acceptance failure — and step 5 (AI fields
and work note on the incident) is consequently not reached in this run.

**Before the fix**, the identical path failed at step 2. Incident `INC0010027`
(03:38:14 UTC) fetched its token (`200`) and then the webhook answered
`422 CONTRACT_VALIDATION_FAILED` with `body.contract_version: Field required`;
a hand-built payload carrying `contract_version: "v1"` returned `202`. That is the
gap #137 describes, and this branch closes it by defaulting the field to `v1`.

**How the run was executed.** The backend image for the run was built from this
branch (`91a5991`), because `main` still rejects the four-field payload; the
container was swapped onto the backend's published port for the few minutes the run
needed, then removed and `barq-api` restarted — `/ready` returned `200` from the
host and from the public address immediately afterwards. `barq-api` runs `main` at
`fc59b9c` throughout. The ServiceNow records on `dev434590` are exactly the setup
described above (token URL, client id and secret, endpoint property); they were
configured before this run and not altered during it.

