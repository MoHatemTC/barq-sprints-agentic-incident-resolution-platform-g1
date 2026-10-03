# Chat milestones 1–2: review and verification

Reviewed GLM's working changes on `feat/admin-chatbot`, based on `946a158`.
The older `chat_review_and_glm_handoff.md` describes the pre-fix snapshot; it is
not a list of currently failing cases.

## Reviewed improvements

GLM removed shared-account conversation takeover, added role enforcement, corrected
blocked-turn persistence and usage-only finalization, introduced bounded model dispatch,
added clarification/topic decisions, bounded retrieval evidence, protected summary
cursors, added pending-request recovery and latest-history pagination, and corrected
the UI image's health check. PostgreSQL integration tests were added but were not run
as part of the default suite.

Additional fixes after review:

- A failed summary read falls back to empty context without an uninitialized-variable crash.
- Serialized memory includes headers/separators within its character limit. Oversized
  latest messages retain their tail and an explicit omission marker.
- Messages omitted by the memory limit can enter the summary before aging out of the
  six-message window. History remains sanitized and stored in PostgreSQL.
- A follow-up without a usable rewrite asks for clarification rather than searching `why?`.
- Guardrails honor the configured chat model. Every model dispatch disables retries and
  has a completion cap; the turn has a six-dispatch maximum.
- Unknown billed usage uses a conservative reservation instead of a chars/4 estimate.
  Mixed proxy reporting preserves reported charges per call. Default allocation is $1/day.
- Pending requests are isolated by conversation; unrelated text cannot reuse an accepted
  request ID. Lost responses and polling timeout retain recovery identity.
- Earlier-message pagination survives reruns; failed-turn feedback survives immediate
  reruns; logout clears pending drafts and UI recovery state.

## Verification

One final `just check` passed: lockfile validation, Ruff lint/format, mypy and pytest.
Results: **1,878 passed, 44 skipped, 29 integration cases deselected**, six existing
warnings, in 87.55 seconds. The mocked API suite separately passed 19 tests outside
the sandbox; the earlier sandbox hang was not reproduced there.

Regression tests first reproduced summary failure, memory overflow, partial price
reporting and model-selection failures, then passed after their fixes. Pending-request
state tests cover same-message retry, cross-conversation preservation and saved-turn
protection. No paid model calls, live ServiceNow writes or database migrations were run.

## Remaining acceptance work and limits

- Run the manual browser E2E suite, particularly follow-ups, topic changes, ambiguity,
  rename/delete, reconnect, token expiry, and long-history pagination.
- Apply migration 0007 only to a designated development database and run the chat
  PostgreSQL integration cases. Default tests do not establish concurrency guarantees.
- Memory uses a configurable character budget, approximately 4k English tokens; exact
  tokenization is not implemented. Summaries/reference resolution remain model-driven.
- Conversation recovery across logout/new browser sessions is not implemented. Hard
  refresh may lose Streamlit session state. Shared credentials are not individual SSO.
- Stale-turn reclamation prevents late answer publication, but there is no cancellation
  or heartbeat before every paid dispatch. Summary writes have monotonic cursor protection
  rather than full active-worker fencing. Evaluate this with delayed-worker integration cases.
- Citation verification validates identity and model-declared sufficiency, not independent
  statement-level faithfulness. A semantic judge is not run per answer.
- The API uses synchronous JSON plus polling; progress/final SSE from the original plan
  remains absent. UI state tests do not substitute for browser interaction tests.

M1/M2 code and default checks are ready for user review; browser/database acceptance
is pending. Per the milestone gate, stop here before M3. Next: chat semantic caching
with permission/corpus/model partitions, source revalidation and false-match tests;
then M4 incident reads, M5 confirmed notes, and M6 final integration/demo.
