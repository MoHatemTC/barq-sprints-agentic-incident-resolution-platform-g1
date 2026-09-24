# Sprint 3 Tool Registry and Permission Enforcement

## 1. Purpose

Sprint 3 centralizes agent tool invocation in `ToolRegistry`. The registry is the
server-owned allowlist that binds each callable name to one handler and one
`PermissionClass`. A caller supplies a registered name, trusted execution context, and
handler arguments; it does not supply the effective permission.

Permission enforcement happens in application code before handler dispatch. Prompts,
model output, approval metadata, and tool arguments cannot add a registration or change
its permission. A prompt naming a tool is therefore only text: it does not make that
tool callable.

Graph nodes must not call `IncidentGateway`, `ServiceNowClient`, an HTTP library, or a
ServiceNow endpoint directly. Such calls would bypass allowlisting, approval checks,
and the mandatory enforcement audit. Nodes receive `AgentDependencies.tools` and invoke
ServiceNow operations only through `ToolRegistry`.

## 2. Threat Model

The implemented boundary addresses the following threats:

- **Prompt injection requesting an unapproved tool.** Graph calls use explicit names,
  and the registry resolves only its private registration map. Prompt or model text
  cannot expand that map.
- **Hallucinated or unregistered names.** A syntactically valid but absent name is
  refused as `unknown_tool` before approval lookup or registered-handler/ServiceNow
  dispatch. After that refusal is final, the optional downstream `RefusalExplainer`
  may make an LLM request; that presentation-only request cannot run the handler or
  change the refusal.
- **Caller-forged permission class.** Permission is read from the immutable server
  registration. A `permission_class` value placed in handler arguments cannot downgrade
  a high-risk registration or change the audit record.
- **High-risk invocation without approval.** `HIGH_RISK` dispatch requires an
  affirmative result from the PostgreSQL-backed checker. Missing approval fails closed.
- **Malformed approval-checker result.** The registry validates the concrete result and
  every result field before trusting it. Non-boolean `permitted` values, invalid refusal
  reasons, invalid approval identifiers, and inconsistent permit/refusal combinations
  become `approval_check_failed`; neither the handler nor untrusted result fields reach
  the trusted refusal facts.
- **Malformed execution identity.** The registry accepts production UUID objects and
  valid UUID strings only. Invalid values become a typed `invalid_context` refusal,
  are represented as `<invalid>` in the audit/explainer boundary, and do not reach the
  approval checker or handler.
- **Approval from another execution.** The database query is restricted to the current
  `execution_id`; a row from another execution does not authorize the call.
- **Approval for another tool.** Approval evidence must contain an exact normalized
  `evidence["tool_name"]` match for the registered name being invoked.
- **An older approval followed by a newer reject, cancel, or expire decision.** The
  unique latest relevant row by `decided_at` is authoritative. A newer `rejected`,
  `cancelled`, or `expired` decision refuses the call.
- **Approval database or checker unavailable.** Database, conversion, checker, or
  checker-result errors become `approval_check_failed`; the handler does not run.
- **Audit sink unavailable.** A failure while emitting a permit event becomes an
  `audit_unavailable` refusal. The registry then attempts a refusal event best-effort,
  and the handler remains blocked. Failure to audit a call that is already refused does
  not turn it into a permitted call.
- **Refusal explainer failure or misleading output.** Explanation happens after the
  authoritative refusal. Model errors or invalid output use a deterministic fallback;
  model text cannot authorize, retry, or dispatch the handler.
- **Graph code bypassing `ToolRegistry`.** The graph dependency exposes the registry,
  not the gateway, and an AST-based test scans every module under `src/agent/nodes` for
  forbidden transports, classes, method attributes, endpoint literals, private registry
  authority traversal, and imports of internal registry-composition types.
- **Malicious or sensitive text in tool arguments.** Arguments are deliberately absent
  from enforcement audit events and `RefusalFacts`, so they are not forwarded to the
  explainer. For a permitted call, arguments still reach the registered handler; their
  domain validation and safe transport use remain handler responsibilities.

The design fails closed at the registry boundary. `ToolRegistry` validates only the
structure of the execution identifier in `ToolCallContext`; it does not authenticate
caller identity. Trusted graph/orchestration remains responsible for supplying the
correct execution UUID. The design does not claim to make arbitrary handler arguments
safe, prevent direct calls from every Python module in the repository, or provide an
approval for a different execution, tool, or argument set.

## 3. Implemented Architecture

The ServiceNow call path is:

```text
Graph node
    |
    v
AgentDependencies.tools
    |
    v
ToolRegistry.invoke(...)
    |
    v
registration / permission / approval / audit enforcement
    |
    v
registered IncidentGateway method
    |
    v
ServiceNowClient
    |
    v
ServiceNow
```

`src/agent/dependencies.py` constructs the production registry and injects it as
`AgentDependencies.tools`. `src/agent/tools/servicenow.py` is trusted composition: it
binds fixed names and permissions to `IncidentGateway` methods. The gateway in
`src/agent/servicenow.py` remains a transport, tracing, and error-adaptation layer; it is
not the authorization boundary.

The refusal path is:

```text
Refusal finalized as RegistryRefusalError
    |
    v
structured internal refusal audit
    |
    v
minimal RefusalFacts
    |
    v
RefusalExplainer
    |
    v
plain-language explanation (or deterministic fallback)
    |
    v
RegistryRefusalError remains authoritative
```

The explainer is not part of authorization. It runs only after the refusal and its audit
attempt, and it cannot change the error's reason, cause a retry, or invoke a handler.

## 4. Permission Classes

The exact server-owned classes in `src/agent/tools/permissions.py` are:

- `read`
- `low_risk_write`
- `high_risk`

Each class is assigned in a frozen `ToolRegistration` and copied into the registry's
private read-only mapping during construction. The caller cannot provide or downgrade
the effective class. Prompts and model output have no registration API and cannot modify
it. After construction, normal assignment or deletion cannot replace the registration
mapping, approval checker, audit sink, or refusal explainer. The copied registration
mapping cannot be mutated in place. This sealing is a runtime authority invariant, not
an attempt to defeat deliberate `object.__setattr__` or other Python reflection.
`HIGH_RISK` is the only class that triggers approval lookup; `READ` and
`LOW_RISK_WRITE` still require a successful permit audit before dispatch.

## 5. Current Registered ServiceNow Tools

| Tool | Permission |
| --- | --- |
| `read_incident` | `read` |
| `write_ai_fields` | `low_risk_write` |
| `write_work_note` | `low_risk_write` |
| `write_execution_log` | `low_risk_write` |

`write_ai_fields` preserves the current combined incident PATCH behavior. The `act`
node puts the work note, human-review flag, and AI fields into one
`IncidentUpdatePayload`, then invokes `write_ai_fields` once. Work-note and
human-review content may therefore remain inside that existing payload. Although
`write_work_note` is registered for the gateway operation, the graph does not invoke it
separately today.

`write_execution_log` writes the application's execution log through ServiceNow. It is
**not** the `ToolRegistry` security audit. For every registry decision, the registry
constructs a separate, server-side `EnforcementAuditEvent` and attempts emission. A
permit-side event must be emitted successfully before handler dispatch; refusal-side
emission is best-effort after the refusal is already final.

## 6. Unknown / Unregistered Tool Behavior

`ToolRegistry.invoke` validates the raw name before looking it up. Accepted normalized
tool names match `[a-z][a-z0-9_]{0,127}`. A syntactically valid name that is not
registered is retained as the unknown tool name and refused before registered-handler
or ServiceNow dispatch. That retained name can appear in `RefusalFacts`, the structured
audit, and optional `RefusalExplainer` input. A non-string name or a name outside the
accepted syntax is represented as `<invalid>` in the refusal, audit, and explainer input
rather than echoed. The registry performs no semantic secret detection on otherwise
valid names.

Both paths raise the typed terminal `RegistryRefusalError` with reason `unknown_tool`
and attempt a structured refusal audit. The prompt/model has no mechanism to add a name
to the allowlist, expose a handler, or invoke an unchecked registry path.

The invocation context accepts an `execution_id` as either a UUID object or a nonempty,
valid UUID string, matching the production webhook, worker, graph, database, and
checkpointer contract. Empty, whitespace-only, and malformed UUID strings fail before
approval lookup or handler dispatch with `RegistryRefusalError` reason `invalid_context`.
The invalid value is replaced with `<invalid>` for refusal audit and explanation. This is
structural validation only; the trusted orchestration path remains responsible for
associating the correct execution identity with the call.

## 7. High-Risk Approval Semantics

The current implementation has these exact semantics:

- Rows in the PostgreSQL `approvals` table are authoritative. The registry does not use
  an HTTP approval response as authorization. In particular, the approvals API's
  non-persisted contract-stub response cannot authorize a registry call.
- The checker converts the call's `execution_id` to a UUID and queries only rows whose
  `Approval.execution_id` matches it.
- Tool scope is stored in JSON evidence rather than a dedicated column. A row is relevant
  only when `evidence` is an object and `evidence["tool_name"]` is an exact, normalized
  match for the requested registered tool.
- Extra evidence fields, including a claimed permission class, are ignored for
  authorization and cannot override the registration.
- Approval rows are immutable final decisions. The schema limits decisions to
  `approved`, `rejected`, `cancelled`, or `expired`, and the migration installs a trigger
  that rejects `UPDATE` and `DELETE`.
- Among matching rows, the unique row with the greatest `decided_at` is authoritative.
  An older approval is superseded by a newer relevant decision.
- Dispatch occurs only when that latest relevant row is clearly `approved`. A missing
  match, `rejected`, `cancelled`, `expired`, unknown decision, or ambiguous latest row
  refuses the call.
- If any row returned for the execution has malformed tool-scope evidence, the checker
  fails closed with `approval_scope_invalid`, even if another row might otherwise match.
- A null or non-`datetime` timestamp on any matching row produces
  `approval_ambiguous`. Mutually incomparable timestamps, such as mixed timezone-aware
  and naive values, also produce `approval_ambiguous`. Two relevant rows tied for the
  latest timestamp are ambiguous.
- Database/session failures, invalid execution UUIDs, checker exceptions, and invalid
  checker return values fail closed as `approval_check_failed`.
- At the registry boundary, `permitted` must be exactly `bool`; `reason` must be a
  `RefusalReason` or `None`; and `approval_id` must be a UUID, a UUID-formatted string,
  or `None`. Permit/refusal field combinations must also satisfy the result contract.
  Malformed values are discarded and cannot supply a refusal code or approval fact.

There is no elapsed-time expiry calculation in the checker. `expired` is an explicit
stored decision, not a duration inferred by the registry.

## 8. Structured Security Audit

The registry constructs an internal, server-side `EnforcementAuditEvent` and attempts to
emit it. The default `StructuredLoggingAuditSink` records successfully emitted events
with:

- registered/sanitized tool name;
- server-owned permission class;
- execution and optional correlation identifiers;
- `permitted` or `refused` decision;
- refusal reason, when present; and
- approval identifier, when present.

Tool arguments are not part of the event. This audit is separate from the registered
ServiceNow `write_execution_log` operation.

For an ordinary refusal, the refusal is finalized first, the structured refusal event is
attempted, and only then may the explainer run. If refusal-event emission fails, the
already-final refusal is preserved and no handler can run.

For a would-be permit, the permit event must be emitted successfully before the handler
runs. If that emission raises, the registry creates an `AUDIT_UNAVAILABLE` refusal,
attempts to emit its refusal event best-effort, runs the explainer after that attempt,
and never dispatches the handler.

The enforcement audit cannot depend on ServiceNow network availability. Permit-side
emission through the server-side audit sink must succeed before any registered
ServiceNow handler is dispatched. Otherwise, the system whose access is being controlled
could also suppress the record of the authorization decision.

## 9. Refusal Explainer

`RefusalExplainer` is strictly downstream of policy. It uses the existing structured LLM
client with a schema that permits only one bounded plain-language `message`. It cannot
approve, reconsider, retry, invoke, or change policy. Any model exception, malformed
response, schema failure, or blank response yields a deterministic reason-specific
fallback already attached to `RegistryRefusalError`.

The complete current `RefusalFacts` boundary is exactly:

- `tool_name`
- `permission_class`
- `execution_id`
- `refusal_reason`
- `approval_id`

Beyond the bounded fields above, the following categories are excluded from
`RefusalFacts` and the explainer prompt:

- tool arguments;
- incident identifiers, content, and text;
- work notes;
- AI payload;
- graph state;
- KB/RAG evidence;
- approval evidence;
- approval reason;
- `decided_by`;
- database exception text;
- transport exception text;
- previous prompts; and
- upstream model output.

The registry also keeps tool arguments out of its structured security audit. A sanitized
`<invalid>` placeholder prevents a syntactically invalid raw tool name from reaching the
audit or explainer. A syntactically valid but unregistered `tool_name` is retained and may
originate from caller input; it can therefore appear in the structured audit and the
bounded explainer input. The optional LLM request occurs only after the refusal is final
and cannot cause handler dispatch or alter that refusal.

## 10. Single Call Path / Bypass Prevention

`AgentDependencies` exposes `tools: ToolRegistry`, not `IncidentGateway`. The `load` node
routes `read_incident` through `deps.tools.invoke`. The `act` node routes
`write_ai_fields` and `write_execution_log` through the same method. It does not make a
separate `write_work_note` call; that content remains in the combined incident update.

`IncidentGateway` is transport/adaptation only. It wraps the async client for synchronous
graph execution, tracing, timeouts, and error translation. It does not replace registry
authorization.

An architectural AST/static test scans every Python file under `src/agent/nodes` and
rejects imports or references to the gateway, incident client, KB client, common HTTP
libraries, ServiceNow operation attributes, ServiceNow endpoint literals, the registry's
private authority fields, or `ToolRegistration` imports from the internal registry
module. Direct traversal such as `deps.tools._registrations[...].handler(...)` is
therefore rejected, while normal `deps.tools.invoke(...)` and public `ToolRegistry` use
remain allowed. Additional tests assert that the dependency container exposes no
`servicenow` field and that the public registry API exposes no registered handlers or
unchecked invoke method.

This AST test is an architectural guardrail, not a Python sandbox. Deliberate dynamic
access through `importlib`, `__import__`, `object.__setattr__`, or other reflection is
outside its intended scope.

Legitimate remaining ServiceNow references are outside graph nodes and have bounded
roles:

- low-level transport in `src/agent/servicenow.py` and
  `src/app/clients/servicenow_client.py`;
- trusted registry composition in `src/agent/tools/servicenow.py` and
  `src/agent/dependencies.py`;
- the pre-graph, manual live-script incident-number lookup in
  `scripts/run_agent_live.py`; and
- the separate KB publishing subsystem under `src/app/publishing` and
  `scripts/publish_kb.py`.

## 11. Least Privilege Rationale

- **`read_incident` — `READ`:** retrieves an incident without modifying ServiceNow.
- **`write_ai_fields` — `LOW_RISK_WRITE`:** performs the existing operational incident
  PATCH inside this project's scoped application and ACL boundary, including the current
  combined AI fields, work note, and human-review flag.
- **`write_work_note` — `LOW_RISK_WRITE`:** provides the existing operational work-note
  write inside the same scoped ACL/application boundary, even though graph nodes do not
  separately invoke it today.
- **`write_execution_log` — `LOW_RISK_WRITE`:** writes the application's ServiceNow
  execution-log record; it is not the registry security audit.

`LOW_RISK_WRITE` is relative to this project's defined ACL, identity, and application
policy boundary. It does not mean that ServiceNow writes are inherently harmless or safe
outside that boundary.

## 12. Extension Contract

A future tool requires all of the following:

1. An explicit server-owned tool name.
2. An explicit `PermissionClass`.
3. A narrow handler.
4. Typed arguments.
5. No caller-controlled permission.
6. Execution- and tool-scoped approval when the class is `HIGH_RISK`.
7. Structured security audit before dispatch.
8. Permit, refusal, and direct-bypass tests.
9. Graph access only through `ToolRegistry`.
10. An update to this documentation and its traceability matrix.

A prompt naming a tool does not make the tool callable.

## 13. S3.5 Knowledge-Base Write-Back Decision

**Decision: CASE 2 — no stable registry-facing S3.5 interface.**

The repository already contains a separate KB publishing subsystem:

- `src/app/publishing/servicenow_kb.py` defines `ServiceNowKBClient` and the internal
  per-article publishing function `publish_article(client, article, kb_sys_id,
  category_mapping=None)`;
- `src/app/publishing/payload.py` builds ServiceNow KB payloads;
- `src/app/publishing/provisioning.py` performs publishing preflight/provisioning; and
- `scripts/publish_kb.py` is a corpus-oriented CLI, read-only by default unless
  `--allow-writes` is supplied.

The same publishing shape is present on `main`. No current code, test, TODO, or document
defines an S3.5 `ToolRegistry` identifier, a graph/agent invocation contract, or a narrow
registry-facing handler that owns the required execution context. The existing
`publish_article` name is an internal publishing function that requires a caller-supplied
client and article model; S3.2 does not reinterpret that Python function name as a public
tool identifier.

Accordingly, S3.2 intentionally does not invent a tool name or production handler. The
missing dependency is an S3.5-owned stable server tool identifier plus a narrow, typed
handler contract defining what knowledge content is written and how it is invoked.

When S3.5 defines that boundary, the registration should use `HIGH_RISK`: persistent
knowledge creation or modification can influence future retrieval and downstream agent
behavior. Registration must go through `ToolRegistry`, require a matching PostgreSQL
approval for the execution, require `evidence["tool_name"]` to equal the final S3.5 tool
name, emit the structured enforcement audit, and add permit/refusal and direct-bypass
tests.

**Requirement status: PARTIAL / BLOCKED BY S3.5 INTERFACE.**

## 14. Requirement Traceability Matrix

`DOCUMENTED / NOT IMPLEMENTED` identifies a documentation-only future contract and does
not claim executable runtime functionality or test coverage.

| # | Requirement | Status | Implementation | Tests | Documentation |
| --- | --- | --- | --- | --- | --- |
| 1 | Centralized `ToolRegistry` | COMPLETE | `src/agent/tools/registry.py`; `src/agent/dependencies.py` | `tests/test_tool_registry.py`; `tests/test_agent_bootstrap.py` | `docs/sprint3_tool_registry.md` §§1, 3 |
| 2 | Explicit permission classes | COMPLETE | `src/agent/tools/permissions.py`; `src/agent/tools/registry.py` | `tests/test_tool_registry.py`; `tests/test_servicenow_tool_registration.py` | §4 |
| 3 | Prompt cannot widen permissions | COMPLETE | `src/agent/tools/registry.py`; `src/agent/tools/servicenow.py` | `tests/test_tool_registry.py::test_forged_permission_argument_cannot_downgrade_high_risk`; `tests/test_servicenow_tool_registration.py::test_invocation_argument_cannot_override_registered_permission` | §§1, 2, 4 |
| 4 | Unregistered tool refused before registered-handler/ServiceNow dispatch | COMPLETE | `src/agent/tools/registry.py` | `tests/test_tool_registry.py::test_unknown_tool_is_blocked_without_handler`; `tests/test_nodes.py::test_forbidden_action_does_not_exist` | §6 |
| 5 | High-risk requires PostgreSQL `Approval` | COMPLETE | `src/agent/tools/registry.py`; `src/app/db/models.py` | `tests/test_registry_enforcement.py::test_high_risk_refusal_branches_do_not_dispatch`; `tests/test_registry_enforcement.py::test_database_error_fails_closed_without_dispatch` | §7 |
| 6 | Approval scoped to execution | COMPLETE | `src/agent/tools/registry.py`; `src/app/db/models.py` | `tests/test_registry_enforcement.py::test_approval_query_filters_by_current_execution_id`; `tests/test_registry_enforcement.py::test_high_risk_refusal_branches_do_not_dispatch` (`another-execution`) | §7 |
| 7 | Approval scoped to tool through `evidence["tool_name"]` | COMPLETE | `src/agent/tools/registry.py` | `tests/test_registry_enforcement.py::test_high_risk_refusal_branches_do_not_dispatch` (`another-tool` and malformed-scope cases) | §7 |
| 8 | Latest-decision semantics | COMPLETE | `src/agent/tools/registry.py`; `src/app/db/models.py`; `migrations/versions/0001_create_postgresql_state_schema.py` | `tests/test_registry_enforcement.py::test_high_risk_latest_approved_is_permitted`; `tests/test_registry_enforcement.py::test_high_risk_refusal_branches_do_not_dispatch`; `tests/test_registry_enforcement.py::test_incomparable_approval_timestamps_fail_closed_without_dispatch` | §7 |
| 9 | Structured internal audit | COMPLETE | `src/agent/tools/registry.py` | `tests/test_tool_registry.py::test_read_tool_is_permitted_and_audited_before_dispatch`; `tests/test_tool_registry.py::test_structured_logging_audit_sink_emits_only_safe_semantic_fields` | §8 |
| 10 | Audit-unavailable fail closed | COMPLETE | `src/agent/tools/registry.py` | `tests/test_tool_registry.py::test_permit_audit_failure_blocks_handler`; `tests/test_refusal_explainer.py::test_audit_unavailable_refusal_is_explained_and_handler_remains_blocked` | §8 |
| 11 | Downstream minimal-context `RefusalExplainer` | COMPLETE | `src/agent/tools/refusal_explainer.py`; `src/agent/tools/registry.py`; `src/agent/prompts.py` | `tests/test_refusal_explainer.py::test_successful_explanation_is_attached_after_refusal_audit`; `tests/test_refusal_explainer.py::test_arguments_incident_text_and_work_notes_never_reach_explainer_or_audit` | §9 |
| 12 | Refusal explainer deterministic fallback | COMPLETE | `src/agent/tools/refusal_explainer.py` | `tests/test_refusal_explainer.py::test_all_model_failures_return_fallback_without_changing_refusal`; `tests/test_refusal_explainer.py::test_every_refusal_reason_has_a_specific_static_fallback` | §9 |
| 13 | Four current ServiceNow tools registered | COMPLETE | `src/agent/tools/servicenow.py` | `tests/test_servicenow_tool_registration.py::test_all_four_core_actions_have_exact_server_owned_permissions` | §5 |
| 14 | Graph uses registry only | COMPLETE | `src/agent/dependencies.py`; `src/agent/nodes/load.py`; `src/agent/nodes/act.py` | `tests/test_registry_enforcement.py::test_graph_dependency_exposes_only_the_tool_registry_for_servicenow`; `tests/test_nodes.py::test_registry_invocations_preserve_payload_context_and_single_patch` | §10 |
| 15 | Direct-call bypass tests | COMPLETE | `src/agent/tools/registry.py`; `src/agent/dependencies.py` | `tests/test_tool_registry.py::test_supported_registry_api_does_not_expose_registered_handler`; `tests/test_registry_enforcement.py::test_graph_dependency_exposes_only_the_tool_registry_for_servicenow`; `tests/test_nodes.py::test_registry_invocations_preserve_payload_context_and_single_patch` | §10 |
| 16 | Static architectural boundary test | COMPLETE | Graph-node boundary: `src/agent/nodes/` | `tests/test_registry_enforcement.py::test_graph_nodes_cannot_bypass_the_tool_registry`; import/class-variant tests in the same file | §10 |
| 17 | Unit tests require no live ServiceNow | COMPLETE | Test injection seams in `src/agent/dependencies.py` and `src/agent/tools/servicenow.py` | `tests/test_tool_registry.py`; `tests/test_registry_enforcement.py`; `tests/test_servicenow_tool_registration.py`; `tests/test_refusal_explainer.py`; fakes in `tests/agent_support.py` | §§3, 10 |
| 18 | Audit records tool and permission class | COMPLETE | `src/agent/tools/registry.py::EnforcementAuditEvent`; `src/agent/tools/registry.py::StructuredLoggingAuditSink` | `tests/test_tool_registry.py::test_structured_logging_audit_sink_emits_only_safe_semantic_fields`; `tests/test_servicenow_tool_registration.py::test_all_four_core_actions_have_exact_server_owned_permissions` | §8 |
| 19 | Least-privilege documentation | COMPLETE | Registrations in `src/agent/tools/servicenow.py`; ACL-bound gateway operations in `src/agent/servicenow.py` | `tests/test_servicenow_tool_registration.py` | §11 |
| 20 | Extension contract | DOCUMENTED / NOT IMPLEMENTED | Future contract; no production implementation claimed | No runtime tests claimed | §12 |
| 21 | S3.5 KB write-back registration | PARTIAL / BLOCKED BY S3.5 INTERFACE | Separate existing publishing code in `src/app/publishing/servicenow_kb.py` and `scripts/publish_kb.py`; no registry registration | Existing subsystem tests in `tests/publishing/test_servicenow_kb.py` and `tests/publishing/test_publish_kb.py`; registry permit/refusal/bypass tests cannot be added until the interface exists | §13 |

## 15. Known Limitations

- S3.5 has not defined a stable registry-facing KB write-back tool name or handler.
- Approval tool scope is stored in `Approval.evidence["tool_name"]`; the approval model
  has no dedicated `tool_name` column.
- Approval rows are not consumed after a permitted invocation. The latest matching
  approved decision can authorize subsequent invocations for the same execution and
  tool.
- Approval is bound to execution and tool, not generically to a hash or schema of the
  individual handler arguments.
- The AST architectural boundary test focuses on graph-node modules under
  `src/agent/nodes`; it is not a repository-wide Python import sandbox and does not try
  to prevent deliberate dynamic/reflection bypass.
