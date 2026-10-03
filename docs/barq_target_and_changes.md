# BARQ — target system and the changes to reach it

Draft for Ali's approval, 2026-10-03. Nothing here is built yet. We continue the current
system and change what needs changing; nothing is rebuilt from scratch.

## 1. What the system does at the end

- **Reads every new incident and understands it:** the problem, real urgency (not only the
  priority field), whether others report the same thing, the caller's recent tickets, and the
  knowledge that applies.
- **Decides by itself within rules we set:**
  - the caller can fix it → writes the steps to the caller and resolves;
  - something is missing → asks the caller one question, reads the reply, continues;
  - an engineer must act → assigns the right group, writes the fix and a summary for them;
  - risky, security or outage → goes to a person for approval; never acts alone.
- **Gives instructions and answers only.** It does not change machines or systems.
- **Follows the ticket to the end:** caller confirms or stays silent → closed as resolved by
  AI; caller reopens → an engineer gets it with the full history.
- **Engineers stay in control:** approve, reject, take over, or hand back to the AI with an
  instruction; the AI continues from where it was.
- **Improves:** confirmations, reopens, rejections and engineer edits are recorded; weak
  articles are flagged; engineers' fixes become proposed articles; an admin approves them and
  can change the rules without code.
- **Runs continuously** on the shared EC2, connected to ServiceNow; everything is audited and
  traced.

## 2. What people see

**Incident page (ServiceNow) — engineers only.** One page, no tabs, minimum fields: the
essentials, one AI panel (status, who is in control, urgency and why, the instructions, the
history) and only the buttons that apply now. Engineers also work from incident lists
(*Assigned to my groups*, *Needs my approval*, *Reopened from AI*) and can create incidents
there; the AI picks those up too. The exact fields are decided with Ali field by field, keeping
every rule that depends on the page (mandatory resolution / hold / work-notes fields, routing
fields, our buttons).

**BARQ AI chat (Employee Center portal) — users only.** One chat page where a user raises a
problem (the chat **creates the incident** in ServiceNow under the user's name), gets
instructions, asks questions, follows their tickets, adds information, and reopens or closes
them. It remembers past conversations. Users never see the engineer form. Every ticket action
runs in ServiceNow with that user's own permissions, never the agent's.

**Who sees what is locked by ServiceNow itself:** a user without a role can read an incident
only as its caller, opener or watcher (existing instance ACL, checked 2026-10-03); the chat
applies the same filter again on the server, so a user can never see another user's ticket,
even by asking the AI.

### 2B. Roles (each with a real test user on dev407364)

| Role | Test user | Can | Cannot |
|---|---|---|---|
| Caller | Abel Tuter, David Miller (no roles) | chat; create, follow, add to, reopen, close **own** tickets | see others' tickets; approve; see work notes or AI reasoning |
| Engineer | Beth Anglin (`itil`) + an assignment group | lists and form; create; take over; hand back / run AI again; write to caller | approve AI fixes (unless also approver); change AI rules |
| Approver | `barq.approver` (`itil` + `x_2215032_ai_inc_0.operator`); `incident_operator` holds the role but no `itil`, so it cannot open the form | approve / reject risky AI fixes | change AI rules |
| BARQ admin | a user with `x_2215032_ai_inc_0.admin` (to create) | rules, autonomy, kill switch, approve knowledge proposals | — |
| The agent | `ai_orchestrator_svc` (BARQ AI Agent) | write only the allowed fields, never on a locked ticket | anything else; never an admin login |

A permission test plays every role and proves each "can" works and each "cannot" is refused.

### 2C. Not in this round
**The AI fixing systems itself** (account unlock, catalog requests, standard changes, machine
fixes) is kept as a future addition. Facts gathered for it: no MID Server on the instance;
Password Reset and the Standard Change Catalog are installed. In this round the AI gives
instructions and answers only.

## 2A. Everything connected

**One source of truth: the ServiceNow incident.** Its fields and its conversation (the
customer-visible comments) are the single record that the caller, the engineer and the AI all
read and write. The chat page, the incident page and the agent are three views of it, never
three copies.

- **One conversation everywhere.** A caller's chat about a ticket *is* that ticket's comment
  thread. What the AI or an engineer writes on the incident appears in the caller's chat; what
  the caller types in the chat appears on the incident for the engineer. Engineer-only notes
  (work notes, AI reasoning) are never shown to the caller.
- **Every change is an event.** Caller edits the ticket or replies, engineer edits a field,
  writes to the caller, changes state, takes over or hands back, the ticket is reopened or
  closed → ServiceNow sends one event to the backend, recorded with who did it.
- **The AI reacts according to who is in control** (agent / waiting for approval / engineer):
  - caller edits the description or adds information while the AI owns it → the AI re-reads
    and continues with the new information (it does not start over or repeat questions);
  - engineer edits or writes to the caller → the engineer takes control; the AI goes quiet but
    keeps its history;
  - engineer presses *Run AI again* / *Hand back to AI* (optionally with an instruction) → the
    AI continues from the current state, with everything that happened since as evidence;
  - caller reopens an AI-resolved ticket → engineer, with the AI's summary.
- **Guards:** one actor writes at a time (last re-read before every AI write); the AI never
  reacts to its own writes; a limit on AI re-runs per ticket so nothing loops; every reaction is
  in the audit trail and the trace.
- **Memory is shared:** the chat remembers the user's conversations, and the AI's history per
  incident is what both the engineer's panel and the caller's chat show.

## 3. What exists today and what changes

| Part (today) | Keep | Change |
|---|---|---|
| Agent pipeline (`src/agent`, 11 steps, safety gates, citations) | everything proven | add: triage of real urgency, look-around (similar tickets, caller history), ask-the-caller, hand-over summary, continue after hand-back |
| Agent writes to ServiceNow (`act`, fulfilment, tool registry) | assign, caller comment, resolve, human-lock respect, crash safety | add tools: ask caller (On Hold – Awaiting Caller), hand over; contracts made versioned so new tools do not break old ones |
| Events ServiceNow → backend (business rule on 4 fields; only `incident.created/updated`; DB check constraint) | the minimal-event design, idempotency | becomes versioned and extensible: caller reply, engineer comment, reopen, close, hand-back; old events keep working during the change |
| Approve / Reject / Take over buttons + `BarqBackend` bridge | all | add *Hand back to AI*; buttons move into the new page's AI panel |
| Incident form (default view + our SDK section) | the data fields themselves | redesigned in the repo's SDK app and installed, keeping every rule that depends on the page (mandatory resolution / hold / work-notes fields, category and AI Enabled for routing, our buttons) |
| Kerolos's chatbot (#213: chat graph, memory, answer cache, migrations 0006–0007; operator-only; Streamlit UI) | chat graph, memory, cache, budget, safety screening | identity becomes the logged-in ServiceNow user (through the bridge), answers about the user's own tickets as well as knowledge, ticket actions through ServiceNow with the user's rights; Streamlit removed; UI becomes the portal page |
| Knowledge publishing and capture (S3.5) | as is | engineers' fixes become proposals; outcome scores per article |
| Backups, rollback script, live-test harness | all | harness extended for every new behaviour |

## 4. Order (each step deployed by hand to the shared EC2 and tested live; no merge)

1. **Finish the current core:** failure-hook tests, full gate, deploy #213 head, all live
   scenarios pass, rollback rehearsed once.
2. **Flexible contracts:** the event contract (today exactly four fields, enforced in the
   ServiceNow script action, the webhook schema, the agent state and a database check) gets a
   version 2 with the actor and a registered event type; agent actions likewise; version 1
   keeps working until everything has moved.
3. **Connection core (section 2A):** ServiceNow's own `incident.inserted / commented / updated`
   events (they already carry who acted) and the reopen fields feed our event; control states;
   caller replies and edits continue the AI; engineer edits take control; *Run AI again* /
   hand back; reopen → engineer; loop and self-trigger guards.
4. **Roles and permissions (2B):** test users and groups set up, permission test passes.
5. **Agent intelligence:** urgency triage, look-around (similar tickets, caller history),
   outage and security handling, outcome recording.
6. **Incident page for engineers:** fields agreed with Ali, built in the SDK app, installed;
   list views.
7. **BARQ AI chat for users:** #213's chat behind the bridge with the user's identity, create
   incident from chat, ticket thread = chat, Employee Center page; Streamlit removed.
8. **Improvement loop and admin rules:** article proposals and scores, rules editable by an
   admin.
9. **Clean-up and docs;** #213 ready for Ali's approval.

## 4A. Built to be upgraded at any time

- **Versioned contracts.** Events and agent actions carry a version; a new version is added
  beside the old one, both work until every sender has moved, then the old one is removed.
- **Registries, not hard-coded lists.** Event types, agent tools, triage signals, routing
  (category → group) and autonomy rules are registered entries or settings. Adding one does not
  touch the pipeline, the webhook or the database constraints.
- **Settings over code.** Behaviour that people will want to tune (autonomy level, what needs
  approval, confirmation window, re-run limit, kill switch) lives in settings an admin can
  change, with history and undo.
- **One place per concern.** ServiceNow access only through the client and the bridge; model
  calls only through the model client (the model can be swapped by configuration); prompts in
  one module.
- **Reversible changes.** Every migration has a working downgrade; every ServiceNow change is in
  the repo and the update set; the rollback script returns the shared EC2 to the previous
  version.
- **Tests that catch breakage.** Contract tests on both sides (ServiceNow ↔ backend), the
  scenario suite and the live harness run on every change, so an upgrade fails a test instead
  of breaking production.
- **Documented.** Each part says what it owns and how to extend it.

## 5. Rules while doing it

Shared systems only (dev407364 + shared EC2). The agent never uses an admin account. Every
ServiceNow change lives in the repo and the tracked update set and can be undone. Nothing on
the instance is changed without Ali's go-ahead for that step. One working session at a time.
Teammates are asked not to merge to `main` while the EC2 runs our branch.

## 6. Scenarios (what the tests prove)

Each scenario becomes a unit test and, where marked **L**, a live test on dev407364 with the
role's real test user. "AI" = BARQ AI Agent. The older catalogue in
`barq_agentic_platform_design.md` §8 remains as extra detail; these are the ones this round
must pass.

**U — User in the chat**
- U1 **L** User describes a problem in chat → incident created under the user's name, AI answers in the same chat.
- U2 **L** User asks a knowledge question without a ticket → answer with article reference, no incident created.
- U3 **L** User asks "what happened with my ticket?" → status and last messages of *their* ticket.
- U4 **L** User asks about another user's ticket → refused; nothing revealed.
- U5 **L** User adds information to an open ticket from chat → appears on the incident; AI continues with it.
- U6 **L** User edits their ticket's description → AI re-reads and continues; does not repeat questions.
- U7 **L** User says "it works" → incident closed as resolved by AI.
- U8 **L** User reopens an AI-resolved ticket → goes to an engineer with the AI's summary.
- U9 User closes their own open ticket → closed as cancelled by the caller; AI stops.
- U10 User returns days later → chat remembers the conversation and their tickets.
- U11 Two tickets open at once → chat keeps them apart; each reply goes to the right ticket.
- U12 User asks for a person → AI hands over to the group; engineer's next message appears in chat.

**A — AI understanding and decisions**
- A1 **L** Caller-doable fix, low risk → steps to the caller, resolved by AI.
- A2 **L** Fix needs IT rights → assigned to the right group, fix written for the engineer, not resolved.
- A3 **L** Vague ticket → one clarifying question, On Hold – Awaiting Caller; reply resumes.
- A4 **L** Priority 1 or security → parked for approval, nothing applied.
- A5 Labelled P1 but clearly one user and minor → handled as low risk only if every hard rule holds; noted with reasons; engineer can undo.
- A6 Labelled low but five similar tickets in an hour → raised as a likely outage; parked; engineers told.
- A7 **L** No relevant knowledge → parked for an engineer; their fix becomes a knowledge proposal.
- A8 Low confidence or citations fail verification → parked with the draft.
- A9 **L** No caller or a service account as caller → never resolved; fix written for the engineer.
- A10 Repeat ticket from the same caller (last fix failed) → not the same fix again; engineer.

**E — Engineer**
- E1 **L** Engineer opens the incident page → sees AI panel: status, control, urgency, instructions, history.
- E2 **L** Approve (optionally with edited fix) → applied once; caller sees it in chat.
- E3 **L** Reject with reason → nothing applied; reason recorded.
- E4 **L** Take over → AI stops on its next re-read; never writes again unless handed back.
- E5 **L** Engineer writes to the caller → takes control; message appears in the user's chat.
- E6 **L** Hand back / Run AI again with an instruction → AI continues from the current state with the instruction.
- E7 Engineer edits a field while the AI is mid-run → AI's write refused at re-read; no overwrite.
- E8 Engineer creates an incident from the list → AI processes it like any other.
- E9 Two engineers act at once → first wins; second sees who has it.
- E10 Engineer resolves it themselves → AI stops; their fix proposed as knowledge.

**P — Permissions (every role, live)**
- P1 Caller cannot read, list or chat about others' incidents.
- P2 Caller cannot see work notes, AI reasoning or approval briefs.
- P3 Caller cannot approve, reject, take over or hand back.
- P4 Engineer without approver role cannot approve.
- P5 Agent cannot write a locked ticket, any field outside its list, or use admin.
- P6 Only BARQ admin changes rules, autonomy and the kill switch.

**S — Security and abuse**
- S1 **L** Prompt injection in ticket or chat → blocked before any model; nothing written; flagged.
- S2 Reported attack (phishing clicked, ransomware, account misuse) → security group, parked, no "delete/reinstall" advice, evidence preserved.
- S3 Secrets in text (passwords, tokens) → redacted everywhere: model, logs, traces, chat history.
- S4 Flood of tickets or chat messages from one user → rate-limited; grouped; engineers alerted.
- S5 User claims to be someone else in text → ignored; identity is the ServiceNow login only.

**C — Connection and consistency**
- C1 AI never reacts to its own writes (no loops).
- C2 Re-run limit per ticket reached → engineer, with summary.
- C3 Event arrives twice → processed once.
- C4 Events arrive out of order → state decided from a fresh read, not the event order.
- C5 ServiceNow and backend disagree (e.g. "awaiting approval" with nothing to approve) → released to an engineer with a reason.
- C6 Ticket closed by a person while AI works → AI stops; nothing written.

**F — Failures and recovery**
- F1 Model unavailable → retries, then failed with a visible reason; DLQ; replay works.
- F2 ServiceNow unavailable → retries; no half-written ticket.
- F3 Worker crash mid-run → resumes from checkpoint; nothing applied twice.
- F4 Backend down while user chats → chat says so; nothing lost; message sent when back.
- F5 Kill switch on → events recorded, nothing processed, chat answers "AI paused".
- F6 Deploy and rollback on the shared EC2 → both proven; schema reversible.

**K — Knowledge and improvement**
- K1 Engineer's fix → knowledge proposal → approver publishes → used in the next answer.
- K2 Article retired in ServiceNow → no longer used.
- K3 Reopen after an AI resolution → article and decision scored down; confirmation scores up.
- K4 Admin changes a rule (e.g. category → group) → takes effect without a deploy; change recorded and undoable.

**V — Upgrades**
- V1 Old (v1) events still accepted while v2 is introduced.
- V2 A new event type or tool is added by registration only; existing tests still pass.
