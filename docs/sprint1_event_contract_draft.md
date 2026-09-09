# S1.3 — Eligibility and outbound event contract (DRAFT)

> **Status: draft scaffold, not a deliverable.** Owner is Ahmed Tamer (S1.3, issue #10).
> Nothing here has been executed on a PDI. Treat every value as a proposal to verify.
> This exists because S1.3 had not started with three days left in Sprint 1, and the
> field names it depends on are already fixed by S1.1.

## Requirements

- **FR-03** — a Business Rule evaluates eligibility on insert and on relevant update, and emits nothing when the incident is inactive, unsupported, already processed, already running or human-locked.
- **FR-04** — the outbound event carries event ID, sys_id, number and event type only, never the full incident record.
- **FR-05** — nothing polls. All processing originates from this event.

## The six eligibility conditions

Evaluated against the S1.1 field model, which is live on `main`.

| # | Condition | Field | Emits only when |
|---|---|---|---|
| 1 | Active | `active` | `true` |
| 2 | AI-enabled | `x_2215032_ai_inc_0_ai_enabled` | `true` |
| 3 | Not human-locked | `x_2215032_ai_inc_0_ai_human_lock` | `false` |
| 4 | Not already running | `x_2215032_ai_inc_0_ai_processing_state` | not `in_progress` |
| 5 | Not already processed | `x_2215032_ai_inc_0_ai_processing_state` | not `complete`, `failed` or `awaiting_approval` |
| 6 | Supported category | `category` | in the configured list |

Conditions 4 and 5 read the same field but are distinct requirements, so the draft reports them as separate rejection reasons — a run that was skipped because it was mid-flight is a different operational event from one skipped because it finished last week.

Scripts must compare **internal choice values**, never display labels.

## Event payload

```json
{
  "event_id":   "a1b2c3d4e5f6...",
  "sys_id":     "9c573169c611228700193229fff72400",
  "number":     "INC0010042",
  "event_type": "incident.created"
}
```

`event_type` is `incident.created` on insert and `incident.updated` on a relevant update.

**Why it is this small.** The backend then retrieves what it is authorised to retrieve, so the event leaks nothing if intercepted and the platform ACLs remain the single source of truth about what the integration may see. It also keeps the contract stable: adding a field to the Incident table does not change the event.

## "Relevant update", and the loop hazard

The rule re-evaluates on update only when one of these changes:

`short_description`, `description`, `category`, `subcategory`, `active`, `x_2215032_ai_inc_0_ai_enabled`

Every AI-written field is **deliberately excluded**. The orchestrator writes back processing state, classification, confidence, suggestion and timestamps. If any of those re-triggered the rule, a single incident would emit events indefinitely — each write triggering the next.

This is the single most important thing to get right and the easiest to get wrong.

## Configuration

Two system properties keep the endpoint and category list out of the exported script:

| Property | Purpose | Suggested default |
|---|---|---|
| `x_2215032_ai_inc_0.event_endpoint` | Webhook URL | *(unset — the rule logs an error and emits nothing)* |
| `x_2215032_ai_inc_0.supported_categories` | Comma-separated allowlist | `hardware,software,network,inquiry` |

## Open items for the owner

1. **Confirm the supported category list** with the mentor. The default above is a guess and drives condition 6.
2. **Wire OAuth.** `setAuthenticationProfile('oauth2', '<sys_id>')` is commented out because the profile does not exist yet — it belongs to S1.2 (#8), which is still in rework. FR-06 requires the least-privilege integration identity, never an admin account.
3. **Verify scope.** The rule must be created with AI Incident Orchestrator active so it exports inside the scoped update set. A scoped app cannot use a *before* rule to abort writes on the global Incident table; an *after* rule that reads and emits is the supported pattern.
4. **Decide async vs sync.** The draft uses `executeAsync()` so the Incident transaction never blocks on the network. Confirm that is acceptable — it means a transport failure surfaces in the ECC queue rather than to the user.
5. **Prove emission.** Sprint 1 has no receiving webhook; that is Sprint 2. Point `event_endpoint` at a request-inspection endpoint and capture the received payload as evidence.
6. **Test all six rejections.** Each condition needs an incident that trips it and produces no event. A rule that emits correctly but fails to stay silent is the more dangerous half.

## Related

- Field model: [`docs/sprint1_field_model.md`](sprint1_field_model.md)
- Draft script: [`servicenow/ai_incident_orchestrator/business_rules/s1_3_eligibility_event.js`](../servicenow/ai_incident_orchestrator/business_rules/s1_3_eligibility_event.js)
- Issues: #10 (S1.3), #8 (S1.2 — supplies the OAuth identity this depends on)
