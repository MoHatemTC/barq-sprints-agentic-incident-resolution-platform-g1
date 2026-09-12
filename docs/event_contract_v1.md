# Outbound Event Contract v1

- Contract version: `v1`
- Producer: ServiceNow AI Incident Orchestrator
- Consumer: downstream incident-resolution backend/webhook
- Trigger: an eligible Incident insert or eligibility-relevant update

## Payload

The authoritative v1 payload contains exactly four top-level fields:

| Field | Meaning |
|---|---|
| `event_id` | Unique identifier created for each eligible emission with `gs.generateGUID()`; the downstream idempotency key. |
| `sys_id` | Source Incident `sys_id`. |
| `number` | Source Incident number. |
| `event_type` | `incident.created` for an eligible insert or `incident.updated` for an eligible relevant update. |

```json
{
  "event_id": "0123456789abcdef0123456789abcdef",
  "sys_id": "abcdef0123456789abcdef0123456789",
  "number": "INC0010001",
  "event_type": "incident.created"
}
```

No additional Incident fields are allowed. The full Incident record must not be
sent.

## Eligibility boundary

The after Business Rule runs on Incident insert and update at order `100`.
Updates are eligible for evaluation only when at least one of these fields
changes:

- `active`
- `category`
- `x_2215032_ai_inc_0_ai_enabled`
- `x_2215032_ai_inc_0_ai_processing_state`
- `x_2215032_ai_inc_0_ai_human_lock`
- `x_2215032_ai_inc_0_ai_retry_count`

Supported categories come from the scoped system property
`x_2215032_ai_inc_0.s1_3_supported_categories`. Its current Sprint 1 default,
aligned with the S1.4 corpus, is `software,network,hardware,inquiry`; the
property remains the configuration source of truth rather than a fixed list in
either Business Rule.
`pending` is eligible. On update, a retry event is eligible only for a new
transition into `failed`; a record already in `failed` does not emit another
retry because of an unrelated or otherwise relevant update. The after rule uses
the previous retry count when deciding whether that new failed transition has a
retry available. Invalid or negative retry counts fail closed as
`invalid_retry_count`.

The retry policy is one original attempt followed by at most two retries:
previous retry count `0` permits Retry 1 and is advanced to `1`; previous retry
count `1` permits Retry 2 and is advanced to `2`; previous retry count `2` or
greater permits no new outbound event. A separate before-update Business Rule,
order `90`, owns this advancement. It acts only on a new transition to `failed`
and consumes a retry only when the Incident is active, AI-enabled, in a supported
category, and not human-locked. Invalid counts or exhausted retries set
`x_2215032_ai_inc_0_ai_human_review_required` to `true`. The rule uses
`current.setValue(...)` within the pending update and never calls
`current.update()`, `GlideRecord.update()`, or `setWorkflow(false)`.

## Asynchronous delivery

The eligibility Business Rule performs no HTTP. It queues the registered Event
`x_2215032_ai_inc_0.s1_3_outbound_event` on the Incident with:

- `parm1`: generated `event_id`
- `parm2`: derived `event_type`
- `current`: the Incident

The Script Action `AI Incident Orchestrator - Send S1.3 Event` explicitly
converts `event.parm1` and `event.parm2` to strings, reconstructs the exact
four-field payload, and sends it with:

```javascript
new sn_ws.RESTMessageV2('AI Incident Orchestrator S1.3 Event', 'post')
```

It serializes the payload with `JSON.stringify`, calls `setRequestBody`, then
`execute()`. It logs transport success or failure and does not write Incident
retry state.

## Sprint 1 transport boundary

The `post` method uses HTTP `POST`, `Content-Type: application/json`, no
authentication, and the approved Sprint 1 request-inspection endpoint. Formal
outbound OAuth is explicitly deferred to Sprint 2 per team confirmation. No
credentials are embedded in this contract or payload.
