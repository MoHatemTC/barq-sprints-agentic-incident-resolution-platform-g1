# S1.1 ServiceNow Implementation Runbook

This runbook implements only Ali's S1.1 assignment. It does not take ownership of the AI Execution Log, OAuth identity/ACL package, Sprint 2 Business Rule, outbound REST message, or backend work unless the mentor explicitly reassigns those items.

## 1. Resolve the schema decisions

Before finalizing fields, ask the mentor for:

- the W0.5 CAD design notes and technical naming convention;
- the classification vocabulary;
- whether AI Enabled is required now;
- the meaning of Human Review;
- the Incident form view to configure;
- ACL ownership and the secondary import-test instance.

The scoped application and draft field records may be started while these answers are pending. Do not invent final taxonomy values or submit unresolved placeholders.

## 2. Create the scoped application

1. Sign in to the development PDI with an administrator/developer account.
2. Open **System Applications > Studio**.
3. Create an application named **AI Incident Orchestrator**.
4. Record the generated scope and application sys_id.
5. Confirm the application picker shows AI Incident Orchestrator.
6. Create/select an in-progress update set named **BARQ G1 - S1.1 - AI Incident Orchestrator** in the same application scope.
7. Confirm both pickers before creating any artifact. Never use the Global/default update set.

## 3. Create scoped columns on Incident

Create the fields listed in `docs/sprint1_field_model.md` on Incident `[incident]` while the scoped application is active. Allow ServiceNow to generate the scope-prefixed column names, then replace every `TBD_FROM_PDI` in the dictionary with the exact names.

Recommended creation order:

1. AI Enabled, if approved
2. AI Processing State and its choices
3. AI Classification
4. AI Confidence
5. AI Suggestion and AI Resolution
6. AI Model Name and AI Agent Version
7. AI Processing Start and AI Processing End
8. AI Human Review Required and AI Human Lock
9. AI Failure Reason

Choice values for AI Processing State must be exactly:

| Label | Internal value | Sequence |
|---|---|---:|
| Pending | `pending` | 100 |
| In Progress | `in_progress` | 200 |
| Awaiting Approval | `awaiting_approval` | 300 |
| Complete | `complete` | 400 |
| Failed | `failed` | 500 |

## 4. Add server-side confidence validation

Create a scoped **before insert / before update** Business Rule on Incident. Run it only when AI Confidence is not empty and changes. Copy the reference logic from `servicenow/ai_incident_orchestrator/reference/validate_ai_confidence.js`, replacing the placeholder with the exact ServiceNow column name.

The rule must accept 0 and 1, reject values below 0 or above 1, show a clear error, and abort the invalid database write. Client-only validation is insufficient because later integrations write through the Table API.

## 5. Configure the Incident form

Create a section named **AI Incident Orchestrator** on the mentor-approved form view.

Recommended two-column order:

| Left | Right |
|---|---|
| AI Enabled (if approved) | AI Processing State |
| AI Classification | AI Confidence |
| AI Model Name | AI Agent Version |
| AI Processing Start | AI Processing End |
| AI Human Review Required | AI Human Lock |

Place AI Suggestion, AI Resolution, and AI Failure Reason below the compact fields with enough width to read long text. Do not place an editable field twice on the same form.

## 6. Test on the source PDI

Use a dedicated test Incident and record its number.

- Confirm all expected fields and choices appear.
- Save confidence values `0`, `0.50`, and `1`; all must succeed.
- Attempt `-0.01` and `1.01`; both must fail without changing the record.
- Save different Suggestion and Resolution values; confirm neither overwrites the other.
- Save start/end timestamps and confirm they can be filtered/reported.
- Set Failed plus a Failure Reason and confirm it persists.
- Verify Human Lock is human-writable and intended to be integration-read-only after ACL integration.
- Verify every field, choice, form section, and Business Rule shows the application scope.
- Inspect the Global/default update set and confirm it contains no S1.1 records.

## 7. Capture evidence

Store screenshots under `docs/screenshots/s1-1/` using the names listed in its README. Screenshots must show the browser address/instance context where practical, readable labels and values, and no passwords, tokens, client secrets, or other sensitive data.

## 8. Package the scoped application

1. Review the working update set's Customer Updates.
2. Check that the dictionary entries, choices, form/view records, validation rule, and application metadata are present.
3. Publish **AI Incident Orchestrator** to an update set if the PDI provides that action; this captures the complete current application version.
4. Set the final update set to Complete.
5. Use **Export to XML**.
6. Save it as `servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml`.
7. Never hand-edit the exported XML.

## 9. Verify on a secondary PDI

Best option: use a clean teammate/mentor PDI running the same ServiceNow family release and on which the application is not already installed. Do not use a production/customer instance.

On the secondary PDI:

1. Elevate to `security_admin` if required.
2. Open **System Update Sets > Retrieved Update Sets**.
3. Select **Import Update Set from XML** and upload the exported file.
4. Open the retrieved update set and run **Preview Update Set**.
5. Treat preview errors, missing dependencies, or skipped records as defects; fix them in the source application and export again.
6. Commit only when preview is clean.
7. Confirm the application scope, Incident fields, choices, form section, and confidence validation exist and function.
8. Record the target instance, preview/commit result, and test date in the field dictionary.

If no clean second PDI is available, ask the mentor to supply one or approve a teammate's PDI. Do not claim the clean-import success standard without performing this test.

## 10. Git and submission

1. Work on `feature/s1-1-ai-incident-field-model`.
2. Add the final XML, completed dictionary, and screenshots.
3. Run `rg -n 'TBD|REPLACE_WITH' docs servicenow` and resolve every submission placeholder.
4. Inspect `git diff --check` and `git status --short`.
5. Commit and push the feature branch.
6. Open a pull request into `main`; do not push the task directly to `main` even though it is currently unprotected.
7. Ask at least one teammate to review scope, naming, and repository placement.
8. Send the PR/repository links to Sarah Nader and update Airtable to the appropriate ready-for-review status.

