# S1.3 Eligibility and Outbound Event Verification

Status: **LIVE PDI BEHAVIOR VERIFIED; PERFORMANCE EVIDENCE PENDING**

## Evidence boundary

The results below reflect the confirmed live PDI execution supplied for S1.3.
No raw screenshots, timestamps, Incident identifiers, request-capture links, or
performance measurements were supplied for this repository update, so none are
invented here. The exported update set is configuration evidence, not a
substitute for runtime evidence.

Sprint 1 transport is an unauthenticated request-inspection endpoint. The
configured REST Message uses `POST` and `Content-Type: application/json`.
Formal outbound OAuth is deferred to Sprint 2 per team confirmation; the old
OAuth expectation is not an S1.3 Sprint 1 acceptance criterion.

## Implemented architecture

1. `AI Incident Orchestrator - Retry Escalation` runs before Incident update at
   order `90`. It acts only on a new transition to `failed`. For an otherwise
   eligible transition it advances retry count `0 -> 1` or `1 -> 2` with
   `current.setValue`. Invalid counts and counts of `2` or greater set AI Human
   Review Required to true. It performs no recursive write.
2. `AI Incident Orchestrator - Evaluate Eligibility` runs after Incident insert
   and update at order `100`. It filters irrelevant updates, evaluates the
   current record, uses the previous count for a new failed transition, refuses
   to re-emit for `failed -> failed`, generates a GUID for eligible emissions,
   and performs no HTTP.
3. The eligibility rule queues
   `x_2215032_ai_inc_0.s1_3_outbound_event` on the Incident with the event ID in
   `parm1` and event type in `parm2`.
4. The registered Script Action `AI Incident Orchestrator - Send S1.3 Event`
   reconstructs exactly `event_id`, `sys_id`, `number`, and `event_type`, then
   sends the JSON through REST Message `AI Incident Orchestrator S1.3 Event`,
   method `post`.
5. The Script Action logs HTTP success/failure but does not write Incident retry
   state.

This event-queue/Script Action split makes outbound delivery asynchronous with
respect to the Incident save.

## Approved eligibility semantics

Relevant update fields:

- `active`
- `category`
- `x_2215032_ai_inc_0_ai_enabled`
- `x_2215032_ai_inc_0_ai_processing_state`
- `x_2215032_ai_inc_0_ai_human_lock`
- `x_2215032_ai_inc_0_ai_retry_count`

Supported categories are `software`, `hardware`, `network`, and `database`.

| Condition | Result |
|---|---|
| Inactive | Suppress as `inactive` |
| AI disabled | Suppress as `ai_disabled` |
| State `complete` | Suppress as `already_processed` |
| Unsupported category | Suppress as `unsupported_category` |
| State `in_progress` | Suppress as `already_running` |
| State `awaiting_approval` | Suppress as `awaiting_approval` |
| Unknown processing state | Suppress as `invalid_processing_state` |
| Invalid or negative retry count | Suppress as `invalid_retry_count` |
| New transition to `failed`, previous retry count >= 2 | Suppress as `retry_limit_exhausted` and require human review |
| Record already `failed` | Do not emit another retry event |
| Human lock true | Suppress as `human_locked` |
| Malformed human lock | Suppress as `invalid_human_lock` |
| State `pending`, all other checks pass | Eligible |
| New transition to `failed`, previous retry count 0 or 1, all other checks pass | Advance the count and emit one retry event |

The retry model is one original attempt plus two retries. A new failed
transition with previous count `0` advances to `1` and permits Retry 1. A new
failed transition with previous count `1` advances to `2` and permits Retry 2.
A later failed transition with previous count `2` emits nothing and requires
human review.

## Live PDI result matrix

`PASS — live PDI` means the behavior was actually exercised in the PDI and
confirmed for this update. `NOT LIVE-VERIFIED` means it was not included in the
reported live evidence, even if the local source or automated tests cover it.

| ID | Verification | Expected observation | Current result |
|---|---|---|---|
| EL-01 | Inactive suppression | `inactive`; no outbound request | **PASS — live PDI** |
| EL-02 | AI-disabled suppression | `ai_disabled`; no outbound request | **PASS — live PDI** |
| EL-03 | Already processed | `already_processed`; no outbound request | **PASS — live PDI** |
| EL-04 | Unsupported category | `unsupported_category`; no outbound request | **PASS — live PDI** |
| EL-05 | Already running | `already_running`; no outbound request | **PASS — live PDI** |
| EL-06 | Awaiting approval | `awaiting_approval`; no outbound request | **PASS — live PDI** |
| EL-07 | Human lock | `human_locked`; no outbound request | **PASS — live PDI** |
| EL-08 | Invalid processing state | `invalid_processing_state`; no outbound request | **NOT LIVE-VERIFIED** |
| EL-09 | Invalid retry count | `invalid_retry_count`; no outbound request | **NOT LIVE-VERIFIED** |
| EL-10 | Malformed human lock | `invalid_human_lock`; no outbound request | **NOT LIVE-VERIFIED** |
| EL-11 | Eligible insert | One queued `incident.created` emission | **PASS — live PDI** |
| EL-12 | Eligible relevant update | One queued `incident.updated` emission | **PASS — live PDI** |
| EL-13 | Irrelevant update | No emission | **PASS — live PDI** |
| RT-01 | New failed transition, previous retry count 0 | Count advances `0 -> 1`; Retry 1 event sent | **PASS — live PDI** |
| RT-02 | New failed transition, previous retry count 1 | Count advances `1 -> 2`; Retry 2 event sent | **PASS — live PDI** |
| RT-03 | New failed transition, previous retry count 2 | No outbound event; `retry_limit_exhausted`; human review required | **PASS — live PDI** |
| RT-04 | Exhaustion escalation | AI Human Review Required set true | **PASS — live PDI** |
| CT-01 | Minimal payload | Exact key set: `event_id`, `sys_id`, `number`, `event_type` | **PASS — live PDI capture** |
| CT-02 | Event ID uniqueness | Separate eligible emissions have distinct IDs | **PASS — live PDI** |
| TX-01 | Outbound transport | Request-inspection receiver returns HTTP 200 | **PASS — live PDI** |
| PERF-01 | Incident-save performance | Real baseline/post measurements and deltas | **NOT YET VERIFIED** |

## Evidence still to retain

For an auditable handoff, attach or link the existing live artifacts when they
are available: PDI/build reference, execution time window, source Incident
identifiers, suppression logs, zero-request captures for negative cases,
sanitized positive request bodies, distinct event IDs, and HTTP status evidence.
Do not retain credentials, cookies, or tokens.

The following outcomes were not part of the supplied live evidence and must not
be relabeled as live-verified without execution artifacts:

- invalid processing state;
- invalid retry count; and
- malformed human lock.

## Performance evidence

Performance is intentionally incomplete. No measured baseline or post-change
save durations are present in the repository. Keep PERF-01 pending until real
measurements, summary statistics, deltas, and confounders are recorded. Do not
infer performance from the asynchronous architecture or from HTTP 200 results.
