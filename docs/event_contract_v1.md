# Outbound Event Contract v1

- Contract version: `v1`
- Producer: ServiceNow AI Incident Orchestrator
- Consumer: downstream incident-resolution backend/webhook
- Trigger: an eligible Incident insert or eligibility-relevant update

## Payload

The v1 payload contains exactly four top-level fields:

| Field | Meaning |
|---|---|
| `event_id` | A unique identifier generated for each logical emission and intended as the downstream idempotency key. |
| `sys_id` | The ServiceNow Incident `sys_id`. |
| `number` | The ServiceNow Incident number. |
| `event_type` | Either `incident.created` for an eligible insert or `incident.updated` for an eligible relevant update. |

```json
{
  "event_id": "0123456789abcdef0123456789abcdef",
  "sys_id": "abcdef0123456789abcdef0123456789",
  "number": "INC0010001",
  "event_type": "incident.created"
}
```

No additional Incident fields are allowed in v1. Full Incident records must
never be transmitted.

Transport authentication is OAuth. Outbound transport wiring and OAuth
execution are handled separately and are not implemented or verified by this
event-preparation phase.
