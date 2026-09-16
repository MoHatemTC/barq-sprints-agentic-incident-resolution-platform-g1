# KB publisher identity (#91)

`scripts/publish_kb.py` signs in as a dedicated, non-admin user, **`kb_publisher`**, which holds one role: **`x_2215032_ai_inc_0.kb_publisher`**. The incident integration user (`ai_orchestrator_svc`) has no knowledge-base rights, and the publisher has no incident rights.

## The role

Shipped in `servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_4_kb_publisher.xml`. Everything is in scope `x_2215032_ai_inc_0`.

| Grant | Why |
|---|---|
| contains `knowledge` | The target knowledge base (*Knowledge*, `dfc19531bf2021003f07e2c1ac0739ab`) lets users with this role contribute, which gives create, read and update on its articles. |
| contains `snc_platform_rest_api_access` | Table API access. |
| `sys_dictionary`, `sys_dictionary.*` read, condition `name=kb_knowledge^elementSTARTSWITHx_2215032_ai_inc_0_` | The preflight checks that the five app columns exist. It can see only those dictionary rows. |
| `kb_category` create | Creates a missing category under the target knowledge base. |
| `kb_version` read, write | Sets the version number shown for an article. |
| `kb_knowledge.workflow_state` write and create, condition `kb_knowledge_base=dfc19531…^x_2215032_ai_inc_0_source_idISNOTEMPTY` | The platform ACL on this field allows nobody, and the Table API cannot run the publish flow. The publisher may set the state only on corpus articles (those carrying a source id) in the target knowledge base. |

ServiceNow always creates an article as a draft, so the publisher creates it and then updates `workflow_state` to the corpus value (`published` or `retired`).

## Per-instance setup

1. Import the update set after S1.1–S1.3, then deploy the Fluent app, which creates the five `kb_knowledge` columns.
2. On newer PDIs, check that **Allow access to this table via web services** is on for `kb_knowledge` (`sys_db_object.ws_access`), then run `GlideTableManager.invalidateTable('kb_knowledge')`.
3. Create the user and assign the role (admin background script, global scope):
   ```javascript
   var u = new GlideRecord('sys_user');
   if (!u.get('user_name', 'kb_publisher')) {
       u.initialize(); u.user_name = 'kb_publisher'; u.first_name = 'KB'; u.last_name = 'Publisher';
       u.web_service_access_only = true; u.active = true; u.insert();
   }
   u.user_password.setDisplayValue('<set on the instance>'); u.update();
   var role = new GlideRecord('sys_user_role'); role.get('name', 'x_2215032_ai_inc_0.kb_publisher');
   var has = new GlideRecord('sys_user_has_role'); has.addQuery('user', u.sys_id); has.addQuery('role', role.sys_id); has.query();
   if (!has.next()) { has.initialize(); has.user = u.sys_id; has.role = role.sys_id; has.insert(); }
   ```
4. In `.env`, keep the integration user in `SERVICENOW_USERNAME` and set the publisher separately:
   ```
   SERVICENOW_KB_USERNAME=kb_publisher
   SERVICENOW_KB_PASSWORD=<set on the instance>
   SERVICENOW_KB_ID=dfc19531bf2021003f07e2c1ac0739ab
   ```
   The publish report's `username` shows who published.

On `dev407364` the articles were created by a hand-made user named `kb_publisher` whose roles are not in the repository. Give that user the role above, remove any other roles it has, and re-run the publisher to confirm.

## Verification

On `dev434590` (clean install plus this set), 2026-09-16, with `.env` holding `ai_orchestrator_svc` and the `SERVICENOW_KB_*` publisher credentials:

| Check | Result |
|---|---|
| Publish into an empty KB (`--allow-writes`) | 11 created; 10 `published` and `KB0010-v1.0` `retired`; versions match the corpus; `sys_created_by=kb_publisher`; report `username=kb_publisher` |
| Publish again | 11 `unchanged`, no writes |
| `kb_publisher`: incidents | `GET` returns 0 rows; `PATCH` returns 404 |
| `kb_publisher`: execution log `POST`, `sys_user_role` read | 403 |
| `kb_publisher`: dictionary rows for `incident` | 0 rows |
| `kb_publisher`: publish an article without a source id | state stays `draft` |
| `ai_orchestrator_svc`: create a KB article | 403 |

Each missing grant was found by publishing with less: without the dictionary ACL, preflight got 403; without `kb_version` access the version sync failed; without the `workflow_state` ACL, articles stayed in draft.
