# Admin chatbot (internal demo)

For the complete PR scope and next implementation milestones, read the
[implementation and continuation handoff](admin_chatbot_handoff.md).

A knowledge-base assistant for trusted project admins: Streamlit frontend, the
existing FastAPI app, and a **separate LangGraph chat workflow**. It answers
questions over the full permitted KB — including the `category="process"`
manual articles that the incident agent deliberately never sees — with
citations, enforced privacy screening, and a hard daily cost cap.

**Not** provided in this release (the API and UI say so explicitly): incident
search/lookup, work-note drafting/posting, creating or reassigning incidents,
KB publishing through chat, attachments, and per-person SSO. The shared
operator credential identifies the demo operator, not individual people.

## Architecture

```
Streamlit (src/app/chat_ui/app.py)
      |
      v
FastAPI /api/v1/chat  (src/api/routers/chat.py)
      |
      v
Chat graph (src/app/chat/graph.py)   screen -> route -> cache -> retrieve -> answer -> verify -> persist
      |
      +-- Qdrant hybrid retrieval (shared collection, no process exclusion)
      +-- PostgreSQL  (chat_sessions / chat_conversations / chat_turns / chat_messages)
      +-- Redis       (daily budget counter, barq:chat:budget:<UTC date>)
      +-- Redis       (optional answer cache, barq:chat:answers:*)
      +-- LiteLLM     (route + answer calls, usage recorded per call)
```

The incident automation is untouched: `src/agent/retrieval.py` still excludes
`category="process"`; the chat retriever (`src/app/chat/retrieval.py`) simply
does not apply that filter and otherwise reuses the shared hybrid search,
lifecycle (published/human_resolved only — retired and draft are invisible)
and the `restricted` security ceiling.

## Screening and safety (always on, fail-closed)

1. Deterministic pattern screening on the raw message (injection regexes).
2. Deterministic redaction (credentials, emails, phones, IBANs).
3. Enforced residual-PII detection — if the detector cannot run, the turn is
   **blocked with an explicit message**, never sent to the model.
4. Semantic injection classification — same fail-closed behavior.

Only sanitized content is persisted; traces are metadata-only for the
screening stage. Answers are built only from retrieved evidence; the model
must cite chunk ids, unknown citations get exactly one bounded repair, and a
model confidence value is never treated as proof.

## Cost control

- Daily chat allowance: `CHAT_DAILY_BUDGET_USD` (default **1.00 USD**), reset
  00:00 UTC, counted in Redis (`barq:chat:budget:YYYYMMDD`).
- A worst-case estimated cost is reserved atomically (single Lua script) before
  any model call, so concurrent turns cannot overspend the same allocation;
  actual usage is reconciled afterwards (refund on over-reservation).
- Paid processing is **refused** unless both `CHAT_PRICE_INPUT_PER_MTOK` and
  `CHAT_PRICE_OUTPUT_PER_MTOK` are set — they must be the verified Sprints
  proxy rates (see `eval/README.md`), never public list prices.
- The cap covers chat only; incident pipeline and evaluation spend are separate.
- All chat purposes use the configured chat model and completion limit, with SDK
  retries disabled. Unknown billed usage retains a conservative charge; reservations
  are larger than typical actual charges. Missing reported costs do not discard the
  reported costs of other calls. Public pricing is not substituted for proxy rates.

## Follow-ups and memory (milestone 2)

Routing resolves context-dependent questions before retrieval and answering. New-topic
questions search their sanitized original text. Ambiguous references ask a clarification
question without retrieval or answer generation. Recent history and the stored running
summary provide context; KB passages support factual answers.

`CHAT_HISTORY_MESSAGE_LIMIT=6` means six messages, not six exchanges.
`CHAT_MEMORY_BUDGET_CHARS=16000` bounds serialized memory, approximately 4,000 tokens
for typical English text. It is not exact token counting. Summary updates use migration
`0007_chat_history_summary`, preserve a processed-message sequence, and leave that
sequence unchanged on blank/failed model output. Oversized recent messages retain their
tail with an omission marker; original sanitized messages remain in PostgreSQL.

Sessions remain isolated even with shared operator credentials. Logout/new login creates
a new session; individual identity and recovery across logins are not implemented.
Streamlit stores pending request IDs per conversation for retry recovery within the
current browser session. Hard refresh may lose browser session state.

## KB answer caching (milestone 3)

Caching is opt-in with `CHAT_CACHE_ENABLED=true`. Enable that flag in both the API
and every standard ingestion environment; restart the API after changing settings.
Initial settings are similarity threshold 0.95, TTL 3,600 seconds and 64 entries per
permission/model/corpus partition. These are starting values, not calibrated quality scores.

The graph checks exact matches first, then bounded semantic candidates using local
embeddings. Semantic reuse requires compatible IDs, numbers, negation, modal words,
actions and recognized service qualifiers. Permission ceiling, operator subject,
Qdrant endpoint/collection, model, prompt versions/text, embedding models and retrieval
settings isolate cache partitions.

Only successfully published, verified KB answers for context-free questions enter the
shared cache. Personal/redacted questions, record-specific questions, refusals, failed
turns and follow-ups bypass it. For standalone/new-topic requests the answer generator
uses the question and KB evidence, with no conversation history; routing still uses
memory to decide whether the question is context-free. Follow-up memory continues to
work through normal retrieval and generation.

Every hit rereads cited Qdrant payloads and validates content fingerprints, visibility
and lifecycle. The standard `ingest_articles` pipeline advances the corpus revision
before mutation and after successful writes, including the manual ingestion script.
During a write the revision is marked pending; hits and admissions are disabled until
a successful finish. Cache-enabled corpus writers are serialized by a Redis lock.
An ordinary failed run releases its lock and leaves caching pending until a successful
re-ingestion. After a hard writer-process crash, confirm that writer has stopped before
recovering its `barq:chat:answers:writer:*` lock and rerunning ingestion.
If revision invalidation is enabled but Redis is unavailable, the ingestion aborts
before mutation. Direct Qdrant mutations must advance the revision as well; source
checks catch changed cited chunks, but cannot detect newly added uncited information.

Cache outages fall back to ordinary retrieval under the budget gate. Cache hits still
run privacy screening and routing: a typical warm KB turn uses three model calls
instead of four, not zero calls. Turn `usage.cache_status` reports `disabled`, `bypass`,
`miss`, `unavailable`, `exact` or `semantic`; token/cost accounting reflects actual calls.
An exact hit needs no fresh query embedding. No paid provider is used for embeddings.

### Cache E2E checks (development environment)

1. Enable caching with verified prices and a small budget; ask a generic KB question.
   Inspect the returned turn: `cache_status=miss`, citations present.
2. Repeat the exact question in the same session, or a new chat in that session.
   Expect `exact`, the same checked answer/sources and one fewer generation call.
3. Try a paraphrase. Expect `semantic` only if similarity and qualifiers both pass;
   a miss is acceptable for conservative matching. Change P1 to P2 or add negation:
   expect a miss and fresh generation.
4. Ask a context-dependent follow-up or personal question: expect `bypass`.
5. Re-ingest modified KB content in the designated development corpus: expect a miss.
   Retire/remove a cited chunk without ingestion: fresh source validation rejects it.
6. Test cache outages with a mocked backend. Budget-store outage must block paid
   processing even when an answer would otherwise be cached.

## Setup

### Option A — everything in Docker (compose)

```bash
docker compose build api chat-ui          # ui is a separate image stage (Streamlit only there)
docker compose up -d qdrant postgres redis api
docker compose exec api alembic upgrade head   # applies chat tables and summary migration
docker compose --profile ui up -d chat-ui      # Streamlit on :8501
```

The `chat-ui` service sits behind the `ui` profile, so the regular
`docker compose up -d` (api + worker) never starts or requires it. Inside the
compose network the UI reaches the API at `http://barq-api:8000`
(`CHAT_UI_API_BASE` is set for you). `.env` must contain `CHAT_ENABLED=true`
and the chat price rates — the api service inherits them via `env_file`.

### Option B — API in Docker, UI on host

```bash
docker compose up -d qdrant postgres redis api
docker compose exec api alembic upgrade head
uv sync --group chat                     # runtime deps + Streamlit (optional group)
just chat                                # Streamlit UI on :8501
```

In `.env` (see `.env.example` → "Admin chatbot"):

```env
CHAT_ENABLED=true                        # feature-off by default
CHAT_DAILY_BUDGET_USD=1
CHAT_PRICE_INPUT_PER_MTOK=<verified rate>
CHAT_PRICE_OUTPUT_PER_MTOK=<verified rate>
# LITELLM_API_KEY / AGENT_LLM_MODEL as for the incident agent
```

Run (two terminals):

```bash
just run      # API on :8000
just chat     # Streamlit UI on :8501
```

## E2E checklist (milestone 1 review)

1. **Disabled by default** — with `CHAT_ENABLED=false`, `POST /api/v1/chat/sessions`
   answers 503 with "Chat is disabled"; the UI shows the message.
2. **Sign in** — Streamlit login with `barq-operator` + `WEBHOOK_AUTH_TOKEN`;
   the chat session secret is issued once and kept in session memory only.
3. **KB answer with citations** — ask e.g. *"Explain the known error register."*
   The answer cites KB articles; expand **Sources** — manual articles render as
   `KB0704 — Manual §7.4 — <title>` with a verbatim excerpt.
4. **Cross-session isolation** — sign in from a second browser profile; its
   conversation list is empty, and guessing the first session's conversation id
   answers 404.
5. **Idempotency** — replay the same `POST .../messages` body (same
   `request_id`): the existing turn is returned and the model is not called
   again (model_calls stays constant in the turn usage).
6. **Privacy screening** — send a message containing an email address; the
   stored user message shows `***EMAIL***`. Send an injection-pattern message
   ("ignore all previous instructions…"): the turn is blocked, the refusal is
   recorded, and no model call happens (Langfuse shows none).
7. **Budget gate** — set `CHAT_DAILY_BUDGET_USD=0.001` and send a message: the
   turn completes blocked with the budget message and nothing is charged.
8. **Persistence** — restart the API; reopen the conversation in the UI:
   full sanitized history is back. Delete the conversation: 204 and history gone.
9. **Unavailable capabilities** — ask "show me incident INC0010023" or "post a
   work note": a clear "not available in this release" message, no retrieval,
   no answer model call.
10. **Budget accounting** — after a few turns, `GET /api/v1/chat/...` turn
    `usage` shows model_calls, tokens and estimated cost; the Langfuse
    generation for `llm.chat_route` / `llm.chat_answer` carries the same
    tokens/cost.

## API surface (`/api/v1/chat`, bearer + `X-Chat-Session-*` headers)

| Endpoint | Purpose |
|---|---|
| `POST /sessions` | Exchange an operator token for a chat session (secret shown once) |
| `POST /conversations` / `GET /conversations` | Create / list this session's conversations |
| `GET /conversations/{id}/messages` | Paginated sanitized history (limit ≤ 200) |
| `POST /conversations/{id}/messages` | Submit `{content, request_id}`; waits for the checked answer |
| `GET /conversations/{id}/turns/{turn_id}` | Persisted turn status (reconnect recovery) |
| `DELETE /conversations/{id}` | Delete conversation, messages, turns |

Errors use the platform envelope. A duplicate `request_id` returns the existing
turn; a second turn while one is running answers 409; another session's ids
answer 404.

## Known limitations (milestones 1–2)

- Follow-up resolution is model-driven; verify rewritten intent, topic changes and
  clarification with the manual E2E suite before accepting milestone 2.
- Answer caching is opt-in and conservative; context-dependent/personal questions
  bypass it and cache hits still incur screening/routing costs.
- Incident reads (milestone 4) and work notes with human confirmation
  (milestone 5) are refused with explicit messages.
- The Streamlit run is host-local (`just chat`) or the containerized `chat-ui`
  compose profile; both are optional and never required by api/worker images.
- Source links are shown only when a valid mapping exists; none exists today,
  so none are fabricated.
