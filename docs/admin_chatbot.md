# Admin chatbot (internal demo)

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
Chat graph (src/app/chat/graph.py)   screen -> route -> retrieve -> answer -> verify -> persist
      |
      +-- Qdrant hybrid retrieval (shared collection, no process exclusion)
      +-- PostgreSQL  (chat_sessions / chat_conversations / chat_turns / chat_messages)
      +-- Redis       (daily budget counter, barq:chat:budget:<UTC date>)
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

- Daily chat allowance: `CHAT_DAILY_BUDGET_USD` (default **6.00 USD**), reset
  00:00 UTC, counted in Redis (`barq:chat:budget:YYYYMMDD`).
- A worst-case estimated cost is reserved atomically (single Lua script) before
  any model call, so concurrent turns cannot overspend the same allocation;
  actual usage is reconciled afterwards (refund on over-reservation).
- Paid processing is **refused** unless both `CHAT_PRICE_INPUT_PER_MTOK` and
  `CHAT_PRICE_OUTPUT_PER_MTOK` are set — they must be the verified Sprints
  proxy rates (see `eval/README.md`), never public list prices.
- The cap covers chat only; incident pipeline and evaluation spend are separate.

## Setup

```bash
docker compose up -d                     # postgres, redis, qdrant
uv sync --group chat                     # runtime deps + Streamlit (optional group)
uv run alembic upgrade head              # applies 0006_chat_tables
```

In `.env` (see `.env.example` → "Admin chatbot"):

```env
CHAT_ENABLED=true                        # feature-off by default
CHAT_DAILY_BUDGET_USD=6
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

## Known limitations (milestone 1)

- Follow-up questions must be self-contained; reference resolution ("it",
  "that") and conversation summaries arrive in milestone 2.
- No answer cache yet (milestone 3); every knowledge answer costs model calls.
- Incident reads (milestone 4) and work notes with human confirmation
  (milestone 5) are refused with explicit messages.
- The Streamlit run is host-local (`just chat`); a Compose UI profile lands
  with the milestone 6 integration work.
- Source links are shown only when a valid mapping exists; none exists today,
  so none are fabricated.
