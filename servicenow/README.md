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
> Currently, these 5 columns are created manually on the PDI under the **AI Incident Orchestrator** application scope as described in §2. Automated creation via scoped update set export / Fluent definitions is tracked in issue #90.

---

## 2. Setup in a New Instance

When deploying to a fresh ServiceNow instance:

1. Configure the 5 custom columns on `kb_knowledge` under the **AI Incident Orchestrator** application scope (`x_2215032_ai_inc_0`) with the elements, types, and max lengths in the table above.
2. In **Form Designer**, add a **BARQ Metadata** section containing these 5 fields.
3. In **List Layout**, add `Corpus Version` to the visible list columns.

---

## 3. Register OAuth Endpoint on the New PDI

Publishing runs as the dedicated `kb_publisher` user, never as admin or as the incident
integration user. Import `ai_incident_orchestrator/ai_incident_orchestrator_s1_4_kb_publisher.xml`
and create the user as described in
[kb-publisher-identity.md](../docs/sprint-1/s1.4-knowledge-and-qdrant/kb-publisher-identity.md).

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
   SERVICENOW_USERNAME=ai_orchestrator_svc
   SERVICENOW_PASSWORD=<integration-user-password>
   SERVICENOW_KB_USERNAME=kb_publisher
   SERVICENOW_KB_PASSWORD=<kb-publisher-password>
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
