# Outbound Event Contract v1 - DRAFT

Status: DRAFT

- Contract version: `v1`
- Producer: ServiceNow AI Incident Orchestrator
- Future consumer: downstream orchestration webhook

## Allowed Payload Fields

The payload is restricted to exactly these fields:

- `event_id`
- `sys_id`
- `number`
- `event_type`

No other incident fields are allowed in the outbound payload.

`event_id` is unique per emission and is intended to be used by the downstream
consumer as its idempotency key.

## TODO

- TODO: Define the allowed `event_type` values after eligibility and update
  semantics are finalized.
- TODO: Define the OAuth configuration after S1.2 is available.

## Open Decisions / Dependencies

- S1.1 merge into `main`
- Supported incident categories
- Unprocessed/retry semantics
- Relevant-update field set
- Confirmation of native `incident.active`
- S1.2 OAuth configuration
