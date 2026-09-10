# ServiceNow S1.3 Scaffolds

The files in this directory are design scaffolds only. They are not ServiceNow
update-set exports, are not deployable application artifacts, and must not be
copied into an active Business Rule while any `TODO_*` marker remains.

## Incident eligibility Business Rule

`s1_3_incident_eligibility_business_rule.js` is intended to become the script
body of a future Business Rule on `incident` after the outstanding S1.1, S1.2,
and eligibility decisions are resolved. Its intended application is
`AI Incident Orchestrator` in scope `x_2215032_ai_inc_0`.

Safety properties of the current scaffold:

- `SCAFFOLD_READY_FOR_DEPLOYMENT` is hard-coded to `false`, so the script exits
  before reading or changing a record, evaluating eligibility, or logging.
- Unresolved semantics are represented by `null` values and a fail-closed
  assertion. Changing the safety flag alone cannot make the incomplete logic
  run.
- There is no `RESTMessageV2`, HTTP client, event queue, polling, credential,
  OAuth, or Basic Auth implementation.
- The payload builder returns only `event_id`, `sys_id`, `number`, and
  `event_type`. The resulting object is not transmitted or queued.
- No Business Rule record, activation setting, update-set XML, S1.1 field
  definition, or S1.2 OAuth configuration is included.

The intended future Business Rule trigger is insert plus a relevant update.
The update condition cannot be configured until the relevant-update field set
is agreed, so this repository deliberately does not include Business Rule
metadata.

## Unresolved markers

The exact placeholders that must be resolved before implementation work can
continue are:

- `TODO_CONFIRM_ACTIVE_USES_NATIVE_INCIDENT_ACTIVE`
- `TODO_DEFINE_UNPROCESSED_SEMANTICS`
- `TODO_DEFINE_FAILED_RETRYABILITY`
- `TODO_DEFINE_SUPPORTED_NATIVE_CATEGORY_VALUES`
- `TODO_DEFINE_ALREADY_RUNNING_SEMANTICS`
- `TODO_DEFINE_RELEVANT_UPDATE_FIELD_SET`
- `TODO_DEFINE_EVENT_TYPE_VALUES`
- `TODO_SUPPLY_EVENT_ID_FACTORY`
- `TODO_WIRE_OAUTH_TRANSPORT_AFTER_S1_2`

`TODO_DEFINE_FAILED_RETRYABILITY` is separate from
`TODO_DEFINE_UNPROCESSED_SEMANTICS` so the decision about `failed` cannot be
silently hidden inside a broader state predicate. The already-running
predicate is also explicit so no processing-state value is inferred by the
scaffold.
