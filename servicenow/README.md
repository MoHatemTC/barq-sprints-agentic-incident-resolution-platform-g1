# ServiceNow Configuration & Update Sets

This directory contains the official ServiceNow export artifacts required to bootstrap and support the BARQ Knowledge Base integration on any ServiceNow Personal Developer Instance (PDI) or production sub-production instance.

---

## 1. Custom Fields Update Set (`kb_knowledge_custom_fields.xml`)

### Purpose
To avoid hazardous runtime schema alterations (`POST /api/now/table/sys_dictionary`) in ServiceNow's Global scope, the required custom fields on the `kb_knowledge` table are provisioned via this official ServiceNow Update Set.

The update set defines the following custom dictionary columns and their documentation labels on `kb_knowledge`:

| Column Name | Type | Max Length | Mandatory | Purpose |
|---|---|---|---|---|
| `u_source_id` | String | 40 | Yes | Deterministic source identifier (e.g. `barq-kb-001`) for idempotency |
| `u_service` | String | 100 | No | Associated BARQ service name |
| `u_version` | String | 40 | No | Document version tracking |
| `u_security_level` | String | 40 | No | Access classification (e.g. `internal`, `public`) |
| `u_article_number` | String | 40 | No | Canonical BARQ article numbering |

---

## 2. How to Import into a New PDI (10-Second Setup)

When moving to a fresh or new ServiceNow PDI, import this update set **before** running the knowledge publishing pipeline:

1. **Log in** to your ServiceNow instance as an administrator (`admin`).
2. In the filter navigator (top left), type **Retrieved Update Sets** (or navigate to `sys_remote_update_set.list`).
3. Under **Related Links** at the bottom of the list, click **Import Update Set from XML**.
4. Click **Browse...**, select `servicenow/kb_knowledge_custom_fields.xml`, and click **Upload**.
5. Click on the imported update set row: **`BARQ KB Knowledge Custom Fields`** (State will show `Loaded`).
6. In the upper right corner, click **Preview Update Set**.
   - ServiceNow will validate all 10 update records.
   - If any minor collision warning appears, click **Accept Remote Update**.
7. In the upper right corner, click **Commit Update Set**.
8. Once committed, the state changes to `Committed`. All 5 custom columns and labels are now live on `kb_knowledge`!

---

## 3. Register OAuth Endpoint on the New PDI

The publishing pipeline authenticates via OAuth 2.0 Client Credentials / Password Grant using `app.auth.token_manager.ServiceNowTokenManager`:

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

Once imported, run preflight validation and publishing:

```bash
uv run python scripts/publish_kb.py --dry-run
```

If schema columns are present, `ServiceNowProvisioner` will pass schema verification and report:
`schema_verified verified_columns=['u_source_id', 'u_service', 'u_version', 'u_security_level', 'u_article_number']`
