# ServiceNow Configuration & Setup

This directory contains configuration documentation and application artifacts supporting the BARQ Knowledge Base integration on ServiceNow.

---

## 1. Custom Metadata Fields on `kb_knowledge`

To support idempotent publishing, semantic versioning, and provenance tracking, the following custom fields are configured on the `kb_knowledge` table under the **AI Incident Orchestrator** scoped application (`x_2215032_ai_inc_0`):

| Column Name | Element | Type | Max Length | Purpose |
|---|---|---|---|---|
| **Source ID** | `x_2215032_ai_inc_0_source_id` | String | 40 | Deterministic lookup key (e.g. `KB0001-v2.0`) for idempotency |
| **Service** | `x_2215032_ai_inc_0_service` | String | 50 | Associated BARQ microservice or technical domain |
| **Corpus Version** | `x_2215032_ai_inc_0_version` | String | 20 | Semantic version tracking (`1.0`, `2.0`, etc.) |
| **Security Level** | `x_2215032_ai_inc_0_security_level` | String | 50 | Access classification (`internal`, `restricted`, `public`) |
| **Article Number** | `x_2215032_ai_inc_0_article_number` | String | 20 | Canonical BARQ article numbering (e.g. `KB0001`) |

> [!NOTE]
> These 5 columns are declared in the scoped application's Fluent source at
> `ai_incident_orchestrator/sdk-app/src/fluent/kb-knowledge-fields.now.ts` and ship with
> the app, so a clean PDI gets them from the deploy — they are no longer a manual step
> (#90). The table above stays here as the reference for verifying an instance by hand.
> `tests/repo/test_kb_fields_match_publisher.py` fails if the Fluent source, the
> publisher in `src/app/publishing/payload.py` and this table ever disagree.

---

## 2. Setup in a New Instance

When deploying to a fresh ServiceNow instance:

1. Deploy the scoped application. The 5 custom columns on `kb_knowledge` come with it (`src/fluent/kb-knowledge-fields.now.ts`) — no manual field creation is needed. Verify them against the table in §1; `ServiceNowProvisioner.ensure_schema` also checks they exist before publishing and fails loudly if they do not.
2. In **Form Designer**, add a **BARQ Metadata** section containing these 5 fields.
3. In **List Layout**, add `Corpus Version` to the visible list columns.

---

## 3. Register OAuth Endpoint on the New PDI

The publishing pipeline authenticates via OAuth 2.0 Resource Owner Password Credentials (ROPC) grant with refresh tokens using `app.auth.token_manager.ServiceNowTokenManager`:

1. In ServiceNow navigator, search for **Application Registry** (under **System OAuth**).
2. Click **New**, then select **Create an OAuth API endpoint for external clients**.
3. Fill in the fields:
   - **Name**: `BARQ Integration Client`
   - **Client ID**: `barq_oauth_client` (or choose a custom ID)
   - **Client Secret**: Enter your chosen secret (or leave blank to auto-generate and copy it)
   - **Refresh Token Lifespan**: `7776000` (90 days)
4. Click **Submit**.
5. Update your `.env` file:
   ```env
   SERVICENOW_INSTANCE_URL=https://<your-new-pdi>.service-now.com/
   SERVICENOW_CLIENT_ID=barq_oauth_client
   SERVICENOW_CLIENT_SECRET=<your-client-secret>
   SERVICENOW_USERNAME=<your-user>
   SERVICENOW_PASSWORD=<your-password>
   SERVICENOW_KB_ID=<target-kb-sys-id>
   ```

---

## 4. Verification

Once configured, run preflight validation and publishing:

```bash
# Read-only: builds and validates all 11 article payloads locally; makes zero HTTP calls.
uv run python scripts/publish_kb.py --dry-run

# Live preflight & publish: connects to ServiceNow, validates schema/categories, and publishes articles.
uv run python scripts/publish_kb.py --allow-writes
```

`--allow-writes` is deliberately opt-in, matching `scripts/test_client.py`:
this script writes `kb_knowledge` articles **and can create `kb_category` records** on
whichever instance `.env` points at, which on a shared PDI is not something to do by
accident. 

Because `--dry-run` validates payloads in-memory with zero network calls, the live preflight schema
verification against ServiceNow's `sys_dictionary` runs only when `--allow-writes` is provided.

When schema columns are present, `ServiceNowProvisioner` passes live schema verification and logs:
```text
schema_verified verified_columns=['x_2215032_ai_inc_0_source_id', 'x_2215032_ai_inc_0_service', 'x_2215032_ai_inc_0_version', 'x_2215032_ai_inc_0_security_level', 'x_2215032_ai_inc_0_article_number']
```
