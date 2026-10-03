# BARQ agentic incident platform — what is built, how it works, how to run it

Status 2026-10-03. Everything here is on PR #213 (branch `feat/admin-chatbot`), deployed by
hand to the shared EC2 and installed on the shared ServiceNow instance `dev407364`. Nothing
is merged to `main`. The target and the plan behind it: `barq_target_and_changes.md`.

## 1. What the system does

| For | What happens |
|---|---|
| **A user (caller)** | Opens **BARQ AI** in the Employee Center (`/esc?id=barq_ai`). Asks a question and gets an answer from the knowledge base with its sources; the chat remembers earlier conversations. Describes a problem and is offered a ticket, created under their own name. Sees their tickets in plain language ("BARQ AI is looking at it", "Waiting for your answer", "Solved - please confirm"), the conversation on each (never engineer notes), replies, presses *It works, close it* or *Still not working*, *Talk to a person* (the agent stands down, an engineer takes it) or *Cancel this ticket*. Answers show as readable text with each knowledge article listed once. Sees only their own tickets. |
| **The agent (BARQ AI Agent)** | Reads every new ticket, looks around (similar open tickets in the last hour, the caller's tickets in the last week), judges real urgency, then: gives a caller-doable fix and resolves; or asks one question and waits (On Hold - Awaiting Caller); or routes to the right group with the fix and tells the caller who has it; or parks for a person when risk is raised. Continues when the caller answers or an engineer hands it back. Never acts on its own writes, never writes to a locked ticket, never uses an admin login. |
| **An engineer** | Opens a ticket and lands on the **BARQ AI** page: one page, no tabs, the AI card on top (status, who is in control, confidence, the fix with article references, what to do next), then only the needed fields, the conversation and work notes. Buttons appear only when they apply (rules in script include `BarqControl`, re-checked inside each button): *Approve AI fix* / *Reject AI fix* (approvers, while a run is paused), *Take over from AI* (while the agent owns it or a run is paused), *Hand back to BARQ AI* (when engineers own it; what they typed in Work notes is the instruction). AI Resolution is shown as an editable field only while a fix waits for approval; otherwise the card shows the fix. Writing to the caller takes over automatically. |
| **Knowledge owners / admins** | `GET /api/v1/feedback/articles` lists articles weakest first: reopened AI resolutions count against an article, confirmed ones for it; an article at net −2 or below no longer lets the agent resolve on its own. Settings below switch behaviour without code. |

## 2. How it works

```
ServiceNow incident ──(eligibility rule: created / AI enabled / lock / category)──┐
   caller reply, edit, engineer message, reopen, close ──(conversation rule)──────┤
                                                                                  ▼
                                   event, identifiers only (contract v1 / v2 + actor)
                                                                                  ▼
EC2: webhook 202 → Redis/Celery worker → observe-only events: recorded (+ article feedback)
                                       → act events: LangGraph run
   load (+ conversation) → validate → classify → determine_risk (+ look-around, triage)
   → retrieve → diagnose → generate ⇄ verify → safety → confidence → act
   act: suggest / assign / update_caller / ask_caller / resolve, or park for approval
                                                                                  ▼
ServiceNow updated as BARQ AI Agent (field ACLs, Human Lock respected, re-read first)
Engineer buttons / users' page → BarqBackend (server side) → backend operator API
```

Key decisions, each enforced in code or ServiceNow ACLs:

* **One record, many views.** The incident is the single source of truth; the users' chat
  on a ticket *is* its comment thread, the engineer page and the agent read the same record.
* **Who is in control** (design 6.4): the agent while it owns the ticket; an engineer once
  they take over, write to the caller, or the agent hands the fix to them (review flag);
  back to the agent only by *Hand back*. A caller's reply on an AI-resolved ticket reopens it
  for an engineer with the AI fix in a work note.
* **Risk** — raising is code (reported attack → security; burst of similar open tickets or
  outage words → likely outage; repeat from the same caller → approval); lowering a P1 needs
  every hard rule clear **and** a model check, is explained in the work note, and is behind a
  switch. Raised risk never turns into a question to the caller.
* **No loops**: the agent's own writes raise no events; at most 2 questions and 4 messages to
  a caller per ticket; a cache follower never reuses a fix for the same caller.
* **One live run per ticket**: a newer run (caller answer, hand-back) closes any older run of
  the same ticket still paused for approval (decision `cancelled`, run `abandoned`), so it
  can never be approved later.
* **Semantic cache only for new tickets**: it compares ticket text, so only a brand-new
  ticket shares or reuses a fix; anything after a conversation or an engineer's instruction
  runs on its own (live finding: a vague ticket once got another ticket's Outlook fix).

## 3. Switches (EC2 `.env`, no code change)

| Setting | Live value | Effect |
|---|---|---|
| `AGENT_AUTONOMY_LEVEL` | `autonomous` | `off` = kill switch (events recorded, nothing processed); `suggest`; `assist`; `autonomous` |
| `AGENT_REASSESS_PRIORITY` | `true` | allow handling a P1 as low risk under the hard rules |
| `AGENT_OUTAGE_THRESHOLD` | `3` | similar open tickets in an hour that make a likely outage |
| `AGENT_ASSIGNMENT_GROUPS` | network/software/hardware/inquiry → groups | routing |
| `AGENT_SERVICE_ACCOUNT_IDS` | agent + KB publisher | never resolved for these callers |
| `CHAT_ENABLED`, `CHAT_DAILY_BUDGET_USD` | `true`, `5` | users' chat on, daily spend cap |
| `CHAT_PRICE_INPUT_PER_MTOK` / `_OUTPUT_` | `0.5` / `3.0` | budget accounting; **estimates, not verified prices** |

ServiceNow side: engineers' page on/off with
`scripts/servicenow_apply_engineer_view.py --enable | --disable`; users' page with
`scripts/servicenow_apply_user_chat.py [--disable]`; buttons, conversation rule, event
sender with `scripts/servicenow_apply_live_ui.py`.

## 4. What changed on ServiceNow (all reversible; previous values in the change manifest)

Scoped app `x_2215032_ai_inc_0`, update set "BARQ agentic 2026-10-03":
* script include `BarqBackend` (server-side bridge, secret read from a property);
* UI actions Approve AI fix, Reject AI fix, Take over from AI, Hand back to BARQ AI (old
  Approve/Refuse/Capture actions deactivated, not deleted), with their rules in script
  include `BarqControl` (ServiceNow cuts conditions at 254 characters and then ignores
  them, which once showed every button on every incident);
* business rule *BARQ AI - Conversation events*, event `x_2215032_ai_inc_0.barq_event`,
  script action *BARQ AI - Send event v2*, property `agent_user_name`;
* form view **BARQ AI** (`barq_engineer`) with the BARQ AI card macro/formatter, the view
  rule *BARQ AI engineer view* (itil users; the Default view itself is untouched) and a
  view-only UI policy showing AI Resolution only while a fix awaits approval;
* Employee Center page `barq_ai` with widget *BARQ AI Assistant*;
* field write ACLs for the agent role on state, incident_state, assignment_group, comments,
  close_code, close_notes, hold_reason — only while Human Lock is off (two committed update
  sets);
* cross-scope privileges set to Allowed for this app: incident read and create,
  `GlideRecord.setValue`, `GlideRecord.insert`, `ScopedGlideElement`;
* test users `barq.admin` (app admin role) and `barq.approver` (`itil` + approver role: an
  engineer who may approve; `incident_operator` has no `itil` and cannot open the form);
  agent user shown as **BARQ AI Agent**.

## 5. How it is tested

| Level | Command | What it proves |
|---|---|---|
| Unit + contract | `uv run pytest -q` (2,070 pass) | every rule above, incl. v1 event compatibility, triage, conversation, feedback, assist API, follower governance |
| CI on #213 | GitHub | the above plus PostgreSQL/Redis integration, SDK build/audit, CodeQL |
| Live core | `scripts/live/verify_agentic_core.py` | resolve, P1 park → approve / reject, injection, no caller, lock |
| Live connection | `scripts/live/verify_conversation.py` | ask → caller answers → continues; engineer takes over → presses Hand back; caller reopens → engineer; approver presses Approve on the page; engineer presses Take over on a paused run |
| Live engineer page | `scripts/live/verify_engineer_page.py` | the page each role sees: card status and exactly the buttons that apply, per incident state |
| Screenshots | `scripts/live/capture_ui.py` | the engineer page and the users' page as the real users (needs Playwright) |
| Live triage | `scripts/live/verify_triage.py` | attack → person; burst → outage; P1 reassessed; repeat → approval |
| Live permissions | `scripts/live/verify_permissions.py` | 12 role checks as the real users |
| Live users' page | `scripts/live/verify_user_chat.py` | the real widget as a fresh caller, end to end |
| Clean-up | `scripts/live/cleanup_test_incidents.py` | cancels only the harness's own fixtures (never deletes) |

Live runs use real demo users through impersonation; buttons are pressed by submitting the
form as that user, exactly as the browser does. The admin login is for fixtures and setup
only. Evidence of the latest full run: `docs/evidence/agentic_live_2026-10-03/`.

## 5A. Try it yourself (about 15 minutes)

Log in to `https://dev407364.service-now.com` as admin, then use **Impersonate user** (user
menu) for each role. Label your test tickets so they are easy to find and clean up.

1. **As a user** (impersonate `Abel Tuter` or any caller without roles), open
   `https://dev407364.service-now.com/esc?id=barq_ai`.
   * Ask "How do I fix Outlook showing Disconnected?" → an answer with its sources, no ticket.
   * Describe a problem ("My Outlook says Disconnected since this morning, webmail works")
     → *Open the ticket* → the ticket appears under My tickets; within a minute BARQ AI Agent
     answers in the ticket. Press *It works, close it* or *Still not working*.
   * Write something vague ("It does not work, please help") → the agent asks one question;
     answer it in the ticket → it continues.
   * Try *Talk to a person* and *Cancel this ticket* on another ticket.
2. **As an engineer** (impersonate `Beth Anglin`), open any of those incidents: the **BARQ AI**
   page opens with the card on top. Write an Additional comment to the caller → the agent
   stands down (the user sees your message on their page). Type an instruction in Work notes
   and press *Hand back to BARQ AI* → the agent continues with it.
3. **High risk:** as a user, open a ticket that says the whole office is down, or as admin
   create an incident with Impact 1 / Urgency 1 and AI Enabled → it waits for approval.
   As Beth you see no Approve button. Impersonate `BARQ Approver` (`barq.approver`), type
   your fix in Work notes and press *Approve AI fix* (or *Reject AI fix*).
4. **Attack:** a ticket like "I clicked a link in a phishing email and entered my password"
   → raised to security and waits for a person; no advice to wipe anything.

What you should never see: the user seeing work notes or another user's ticket; a button
that does not apply; the agent writing after an engineer took over.

## 6. Deploy and roll back

Deploy (by hand, our branch, no merge): on the EC2 `git fetch origin feat/admin-chatbot`,
`git checkout --detach FETCH_HEAD`, `docker compose build api celery-worker`,
`docker compose run --rm api alembic upgrade head`, `docker compose up -d api celery-worker`,
check `/ready` and the worker ping. Schema is now `0009_article_feedback`; every migration
has a working downgrade (0008's restores the old event check as NOT VALID so rows are kept).

Roll back the EC2 to `main`: `scripts/ops/rollback_shared_ec2.sh <backup-dir>` with the
backup in `~/barq-backups/2026-10-03-pre-agentic/`. It stops the API and worker and saves a
fresh database snapshot first (the older schema cannot keep chat, article-feedback and
event-actor data), then downgrades, restores `.env`, checks out the previous commit and
rebuilds.

Rehearsed 2026-10-03 with `scripts/ops/rehearse_shared_migrations.sh` on an isolated copy of
the shared database: downgrade to `0005`, upgrade to `0009`, restore from the fresh snapshot;
all 280 event rows survived the downgrade and every table matched its original row count and
fingerprint after the restore. This proves the database path, not yet a full live release
rollback (application swap included).

ServiceNow: admin's current update set for the app is back to `Default` (as planned for the
end). Select "BARQ agentic 2026-10-03" again before making further ServiceNow changes, or
they are captured in `Default`.

## 7. Known limits and what is not in this round

* The agent does not change systems (unlock accounts, run fixes); planned as a later round.
* The users' chat creates incidents with impact/urgency 3; the agent's triage decides real
  urgency from the content.
* Kerolos's Streamlit chat UI was removed (Ali rejected Streamlit); the chat API stays and
  the users' page uses it.
* Article feedback starts empty; scores grow with real confirmations and reopens.
* Chat prices are estimates for the budget guard; replace with the provider's prices.
* The backend still has one operator identity; engineers' names travel with each decision.
* While the EC2 runs this branch, a merge to `main` would redeploy `main` and fail on the
  newer schema: hold merges or roll back first.
