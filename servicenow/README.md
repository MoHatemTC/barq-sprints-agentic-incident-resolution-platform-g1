# ServiceNow Knowledge Base Integration Configuration

This document outlines the required configuration on ServiceNow instances to support the BARQ Knowledge Base publishing integration.

---

## 1. Custom Columns on `kb_knowledge`

To support idempotent publishing, document provenance, and version tracking, the following 5 custom columns must exist on the `kb_knowledge` table:

| Column Name | Type | Max Length | Mandatory | Purpose |
|---|---|---|---|---|
| `u_source_id` | String | 40 | No | Deterministic source identifier (e.g. `KB0001-v2.0`) for idempotent publishing |
| `u_service` | String | 50 | No | Associated BARQ microservice or owning technical domain |
| `u_version` | String | 20 | No | Semantic version string of the knowledge article |
| `u_security_level` | String | 50 | No | Access classification (`internal`, `restricted`, `public`) |
| `u_article_number` | String | 20 | No | Canonical BARQ article numbering (e.g. `KB0001`) |

### How to Create Them in ServiceNow Web UI
1. Log in to your ServiceNow instance as an administrator (`admin`).
2. In the filter navigator, type **Tables** (under **System Definition**) or open `sys_db_object.list`.
3. Search for and open the **Knowledge** (`kb_knowledge`) table.
4. In the **Columns** related list at the bottom, click **New** for each column and create:
   - **Type**: String
   - **Column label**: *Label name (e.g. `Source ID`)*
   - **Column name**: `u_source_id` (ServiceNow prepends `u_` automatically)
   - **Max length**: *40*
   - Click **Submit**.
5. Repeat for `u_service` (String 50), `u_version` (String 20), `u_security_level` (String 50), and `u_article_number` (String 20).

---

## 2. Register OAuth Endpoint

The publishing pipeline authenticates via OAuth 2.0 Password Grant using `app.auth.token_manager.ServiceNowTokenManager`:

1. In ServiceNow navigator, search for **Application Registry** (under **System OAuth**).
2. Click **New**, then select **Create an OAuth API endpoint for external clients**.
3. Fill in the fields:
   - **Name**: `BARQ Integration Client`
   - **Client ID**: `barq_oauth_client` (or choose a custom ID)
   - **Client Secret**: Enter your chosen secret (or leave blank to auto-generate and copy it)
   - **Refresh Token Lifespan**: `7776000` (90 days)
4. Click **Submit**.

---

## 3. Environment Variables Configuration

Set the following variables in your `.env` file:

```env
SERVICENOW_INSTANCE_URL=https://<your-instance>.service-now.com/
SERVICENOW_CLIENT_ID=barq_oauth_client
SERVICENOW_CLIENT_SECRET=<your-client-secret>
SERVICENOW_USERNAME=<your-user>
SERVICENOW_PASSWORD=<your-password>
SERVICENOW_KB_ID=<target-kb-sys-id>
```

---

## 4. Verification & Publishing

### Dry-Run Mode (Offline Validation)
Validates local corpus integrity and payload generation without network calls:
```bash
uv run python scripts/publish_kb.py --dry-run
```

### Live Publishing Pipeline
Executes preflight verification and publishes all articles:
```bash
uv run python scripts/publish_kb.py
```

The preflight check will verify that all 5 custom columns exist on `kb_knowledge` and report:
`schema_verified verified_columns=['u_source_id', 'u_service', 'u_version', 'u_security_level', 'u_article_number']`
