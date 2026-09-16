# S1.3 outbound OAuth

The S1.3 outbound event authenticates with **OAuth 2.0 client credentials** (#10, rubric Point 8, #93).

## Flow

1. The script action calls the REST message `AI Incident Orchestrator S1.3 Event` (`post`).
2. The REST message uses the OAuth profile `BARQ Webhook OAuth default_profile`. Before the call, ServiceNow requests a token:
   ```
   POST {backend}/api/v1/oauth/token
   Content-Type: application/x-www-form-urlencoded

   grant_type=client_credentials&client_id=barq-servicenow&client_secret=…
   ```
3. The backend answers `{"access_token": "…", "token_type": "Bearer", "expires_in": 300}`.
4. ServiceNow sends the event with `Authorization: Bearer <access_token>`, and the webhook verifies the token before accepting the event.
5. ServiceNow caches the token until it expires, then requests a new one.

The payload is unchanged: the four fields in [event-contract-v1.md](event-contract-v1.md).

## ServiceNow side (update set)

`servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_3_outbound_oauth.xml`. Import it after `ai_incident_orchestrator_s1_3.xml`.

| Record | sys_id | Values |
|---|---|---|
| `oauth_entity` **BARQ Webhook OAuth** | `c3758576739b4b102aedfed25ab8b780` | type OAuth Provider, client_id `barq-servicenow`, default grant client credentials, credentials sent in the request body, token lifespan 300 s |
| `oauth_entity_profile` **BARQ Webhook OAuth default_profile** | `0b758576739b4b102aedfed25ab8b782` | grant type client credentials |
| `sys_rest_message` **AI Incident Orchestrator S1.3 Event** | `04b4db0d83970710b5309e80ceaad35a` | authentication `oauth2`, profile above; the `post` method inherits it |

The Fluent source in `sdk-app` declares the same records under the same sys_ids. `check_build_matches_export.py` fails CI if the build and the exports disagree on these ids, on the REST message's authentication, or on the provider's grant settings.

### Per-instance configuration

The token URL and the client secret are not in the repository. After installing, an admin sets:

| Where | Value |
|---|---|
| `oauth_entity` BARQ Webhook OAuth → **Token URL** | `{backend}/api/v1/oauth/token` |
| `oauth_entity` BARQ Webhook OAuth → **Client Secret** | the backend's `WEBHOOK_OAUTH_CLIENT_SECRET` |
| Property `x_2215032_ai_inc_0.s1_3_event_endpoint` | `{backend}/api/v1/webhook/incident` |

Re-importing the update set resets the token URL to its placeholder (`https://example.invalid/…`), so set it again afterwards. A Fluent deploy (`now-sdk install`) leaves both the URL and the secret untouched; this was checked on `dev434590`.

## Backend side

`src/app/auth/webhook_oauth.py` provides:

- `create_token_router(get_config)`: the `POST /api/v1/oauth/token` endpoint. It accepts client credentials in the form body or as HTTP Basic. It answers `400 unsupported_grant_type` for anything but `client_credentials`, and `401 invalid_client` for wrong credentials. Responses are `Cache-Control: no-store`.
- `bearer_dependency(get_config)`: a FastAPI dependency for the webhook. It returns the token's claims, or raises `401` with `WWW-Authenticate: Bearer`.
- `WebhookOAuthConfig(client_id, client_secret, signing_key)`: the signing key must be at least 32 characters.

Tokens are HS256 JWTs (`iss`/`aud` `barq-webhook`, `sub` = client id, 5-minute expiry, 30-second clock leeway). They carry no incident data. Settings, from `.env.example`:

```
WEBHOOK_OAUTH_CLIENT_ID=barq-servicenow
WEBHOOK_OAUTH_CLIENT_SECRET=
WEBHOOK_OAUTH_SIGNING_KEY=
```

The S2.1 webhook includes the token router and uses `bearer_dependency` in place of comparing the header with a static token.

## Verification

On `dev434590` (S1.1, S1.2, S1.3, the S1.2 security fixes and this set; access tracking Enforcing), against a receiver built from `webhook_oauth.py` and reached through a temporary tunnel, 2026-09-16:

| Check | Result |
|---|---|
| POST to the webhook from ServiceNow with no token | `401` |
| Eligible incident update (`INC0008001`) | outbound log: `POST /api/v1/oauth/token 200`, then `POST /api/v1/webhook/incident 202`; the receiver verified the token (`sub=barq-servicenow`) and got exactly the four contract fields |
| Wrong client secret on the provider | `POST /api/v1/oauth/token 401`; the event was not delivered, and the script action logged `invalid_client` |
| Cross-scope privileges under Enforcing | none requested |
| `now-sdk install` after configuring | one provider, one profile, REST message still `oauth2`; token URL and secret unchanged |

Unit tests: `tests/auth/test_webhook_oauth.py` covers the round trip, wrong client, expiry, future `iat`, other signing key, tampered claims, `alg: none`, malformed tokens, both credential styles, and a static secret presented as a Bearer token.
