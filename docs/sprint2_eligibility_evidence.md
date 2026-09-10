# S1.3 Eligibility and Outbound Event Verification

Status: **NOT YET VERIFIED**

## Purpose and scope

This document defines verification and evidence for BARQ G1 Sprint 1 task
S1.3. It is an execution template, not evidence that the implementation or any
result already exists.

S1.3 covers:

- an Incident Business Rule on insert and relevant update;
- six eligibility conditions: active, AI-enabled, unprocessed, supported
  category, not already running, and not human-locked;
- no outbound event plus a specific, distinguishable platform-side suppression
  reason when an eligibility condition fails;
- no outbound emission for an irrelevant update;
- an eligible outbound call using `RESTMessageV2` and the S1.2 OAuth
  configuration;
- a payload containing exactly `event_id`, `sys_id`, `number`, and
  `event_type`;
- a unique `event_id` for each logical emission; and
- baseline and post-implementation incident-save performance measurement.

Do not treat suppression text, number of suppression log entries, or
first-failure ordering as fixed by S1.3. The platform-side reason must identify
the failed condition and be distinguishable from the other suppression
reasons. Any evaluation order is implementation-dependent until explicitly
approved.

## Semantic dependencies

| Open dependency | Required before affected test execution | Affected IDs |
|---|---|---|
| Exact native active mapping | Approve the pass and fail values/predicate. | EL-01, EL-07, EL-08, EL-09 |
| Supported native categories | Approve the supported set and an available value outside it. | EL-04, EL-07, EL-08, EL-09 |
| Exact unprocessed predicate | Approve passing/failing state classifications. | EL-03, EL-05, EL-07, EL-08, EL-09 |
| `failed` retryability | Decide whether `failed` passes the unprocessed predicate. | EL-03, EL-05 |
| Exact already-running predicate | Approve passing/failing state classifications. | EL-03, EL-05, EL-07, EL-08, EL-09 |
| Relevant-update field set | Approve at least one relevant field and one field outside the set. | EL-08, EL-09 |
| `event_type` values | Approve the values for insert and relevant update. | EL-07, EL-08, CT-01 |
| S1.2 OAuth details | Identify the approved S1.2 configuration used by the REST message. | EL-07, EL-08, CT-01, SEC-01 |
| Formal performance threshold | Approve the governing metric and threshold; do not infer either. | PERF-01 |

Unresolved semantics remain unresolved in this document. Record the approved
decision reference with the eventual test evidence; do not infer a decision
from field labels, scaffold examples, or this template.

## Requirement and test matrix

For EL-01 through EL-06, construct a fixture in which only the named condition
fails and the other five pass. A test is not valid if isolation cannot be
demonstrated.

| ID | Verification | Fixture and action | Expected result | Current result |
|---|---|---|---|---|
| EL-01 | Inactive suppression | Save an incident that fails only the approved active predicate, on insert or an approved relevant update. | No outbound emission; a specific platform-side reason distinguishes the inactive condition. | **NOT YET VERIFIED** |
| EL-02 | AI-disabled suppression | Save an incident that fails only AI-enabled. | No outbound emission; a specific platform-side reason distinguishes the AI-disabled condition. | **NOT YET VERIFIED** |
| EL-03 | Processed suppression | After the reachability check below, save an incident that fails unprocessed and passes not already running. | No outbound emission; a specific platform-side reason distinguishes the unprocessed/processed condition. | **NOT YET VERIFIED** |
| EL-04 | Unsupported-category suppression | Save an incident that fails only supported category, using an available native category outside the approved set. | No outbound emission; a specific platform-side reason distinguishes the unsupported-category condition. | **NOT YET VERIFIED** |
| EL-05 | Already-running suppression | After the reachability check below, save an incident that passes unprocessed and fails not already running. | No outbound emission; a specific platform-side reason distinguishes the already-running condition. | **NOT YET VERIFIED** |
| EL-06 | Human-lock suppression | Save an incident that fails only not human-locked. | No outbound emission; a specific platform-side reason distinguishes the human-lock condition. | **NOT YET VERIFIED** |
| EL-07 | Eligible insert | Insert an incident that passes all six conditions. | The insert produces the eligible outbound event through `RESTMessageV2`, using the approved insert `event_type`; no eligibility suppression reason applies. | **NOT YET VERIFIED** |
| EL-08 | Relevant update | Change an approved relevant field so the post-update incident passes all six conditions, then save once. | The relevant update produces the eligible outbound event through `RESTMessageV2`, using the approved update `event_type`; no eligibility suppression reason applies. | **NOT YET VERIFIED** |
| EL-09 | Irrelevant update | Change only a field outside the approved relevant-update set and save once. | No outbound emission. Internal Business Rule or evaluator execution is not part of the pass/fail criterion. | **NOT YET VERIFIED** |
| CT-01 | Minimal payload | Capture one eligible receiver request and compare its parsed top-level key set and values with the source incident and approved event type. | The payload contains exactly `event_id`, `sys_id`, `number`, and `event_type`; values are populated and source values match. | **NOT YET VERIFIED** |
| CT-02 | `event_id` uniqueness | Capture at least two separate logical eligible emissions and compare their `event_id` values. | Each emission has a non-empty `event_id`, and the captured IDs are distinct. | **NOT YET VERIFIED** |
| SEC-01 | S1.2 OAuth use | Inspect the S1.3 REST message authentication reference and correlate it with one eligible call. | The `RESTMessageV2` call uses the S1.2 OAuth configuration; S1.3 introduces neither Basic Auth nor hardcoded credentials. | **NOT YET VERIFIED** |
| PERF-01 | Save-performance comparison | Measure comparable incident saves before and after S1.3 by the practical method below. | Report raw data, summary statistics, deltas, and confounders. Formal acceptance remains pending until a threshold is approved. | **NOT YET VERIFIED** |

## Common evidence checklist

For every executed test, retain the applicable evidence below:

- execution ID, date/time and timezone, tester, PDI/environment, build, and
  deployed S1.3 reference;
- incident `sys_id` and number, plus before/after values needed to prove the
  trigger and all six eligibility outcomes;
- the approved semantic-decision references used to construct the fixture;
- time-bounded platform evidence showing the applicable suppression reason,
  outbound activity, or absence of outbound activity;
- receiver capture or receiver zero-result evidence when applicable, correlated
  by source identity, request identifier, and timestamps;
- for a positive emission, the sanitized raw payload and its parsed key/value
  comparison;
- actual observations, anomalies, and confounders rather than assumed values;
  and
- redacted artifacts: never retain access tokens, secrets, cookies, or usable
  credentials.

Use an approved non-production environment and authorized capture endpoint.
Choose a time window long enough for the implemented transport behavior. The
implementation may be asynchronous or synchronous: record which was observed.
If synchronous, include network latency in incident-save timing. Transport mode
alone does not determine S1.3 pass or fail unless a separate requirement is
approved.

Leave a result as **NOT YET VERIFIED** until the test has actually run and its
evidence has been reviewed. Use **BLOCKED / NOT RUNNABLE** only as described for
the EL-03 and EL-05 reachability gates; this classification is not an
implementation failure.

## Special test notes

### EL-03 — processed-state reachability

Before executing EL-03, use the approved state decision table to prove that a
fixture exists which fails `unprocessed` while passing `not already running`.
Retain the selected state and both predicate evaluations. If the final approved
predicates make this combination impossible, record **BLOCKED / NOT RUNNABLE —
pending semantic clarification**. Do not execute a non-isolated substitute and
do not record implementation FAIL.

### EL-05 — already-running reachability

Before executing EL-05, use the approved state decision table to prove that a
fixture exists which passes `unprocessed` while failing `not already running`.
Retain the selected state and both predicate evaluations. If the final approved
predicates make this combination impossible, record **BLOCKED / NOT RUNNABLE —
pending semantic clarification**. Do not make first-failure ordering a
requirement or record an impossible fixture as implementation FAIL.

### EL-08 — relevant update

Retain the approved relevant-field list and a record diff showing the intended
relevant change. Confirm that the post-update state passes all six conditions.
Prepare the fixture outside the observation window so setup saves are not
mistaken for the tested emission.

### EL-09 — irrelevant update

Retain the approved relevant-field list and a record diff showing that only a
field outside that set changed. PASS requires no outbound emission. Do not
require proof that the Business Rule or eligibility evaluator never ran
internally, and do not fail the test merely because internal evaluation ran.

### SEC-01 — authentication boundary

This test proves only that S1.3's `RESTMessageV2` call references and uses the
S1.2 OAuth configuration and that S1.3 adds no Basic Auth or hardcoded
credentials. Retain a sanitized configuration reference and correlated call
evidence. Do not duplicate broader S1.2 security acceptance testing, inspect or
record token values, or add unrelated role and least-privilege criteria here.

### CT-02 — uniqueness boundary

Use two separate logical emissions at minimum; more may be captured if useful.
Map each captured `event_id` to its emission and compare exact values. This test
does not define or verify retry, replay, deduplication, or idempotency policy.
Do not deliberately introduce those behaviors into CT-02.

### PERF-01 — practical measurement method

The following is a proposed practical method, not an S1.3 requirement:

1. Use the same PDI, the same save path, and a comparable incident fixture for
   both phases.
2. Capture the baseline with S1.3 absent or inactive and the post phase with the
   reviewed S1.3 implementation active; record exact configuration/build.
3. Run 5 warm-up saves per phase and exclude them consistently from summary
   statistics.
4. Run 30 measured saves per phase, serially where practical, and retain every
   raw duration, including slow or failed observations.
5. Calculate the median, maximum, and nearest-rank p95 for each phase. With 30
   measurements, p95 is sorted observation `ceil(0.95 * 30) = 29`.
6. Report before/after absolute and percentage deltas for each statistic. If a
   baseline value is zero, report percentage delta as not calculable.
7. Document time windows, background activity, errors, outliers, transport
   mode, and other confounders. Do not silently discard inconvenient samples.

No mentor approval of the sample count is required by this template. Do not
invent a numeric threshold or use the proposed sample method as acceptance
criteria. Until the governing metric and threshold are approved, report the
measurements and mark formal performance acceptance pending. For synchronous
transport, network latency is part of save timing; for asynchronous transport,
record the separation and do not add later network time to save duration.

## Evidence capture

Populate this table only from executed tests. Blank values are intentional.

| ID | Execution/date/build | Actual observations or captured values | Result | Evidence link/attachment | Reviewer |
|---|---|---|---|---|---|
| EL-01 |  |  | **NOT YET VERIFIED** |  |  |
| EL-02 |  |  | **NOT YET VERIFIED** |  |  |
| EL-03 |  |  | **NOT YET VERIFIED** |  |  |
| EL-04 |  |  | **NOT YET VERIFIED** |  |  |
| EL-05 |  |  | **NOT YET VERIFIED** |  |  |
| EL-06 |  |  | **NOT YET VERIFIED** |  |  |
| EL-07 |  |  | **NOT YET VERIFIED** |  |  |
| EL-08 |  |  | **NOT YET VERIFIED** |  |  |
| EL-09 |  |  | **NOT YET VERIFIED** |  |  |
| CT-01 |  |  | **NOT YET VERIFIED** |  |  |
| CT-02 |  |  | **NOT YET VERIFIED** |  |  |
| SEC-01 |  |  | **NOT YET VERIFIED** |  |  |
| PERF-01 |  |  | **NOT YET VERIFIED** |  |  |

## Review rule

Record PASS or implementation FAIL only after the fixture is runnable, the
action is executed, and the expected evidence is reviewed. Where multiple
eligibility conditions fail, evidence may reflect implementation-dependent
evaluation order; such a run does not isolate a suppression condition and must
not be used to pass EL-01 through EL-06.
