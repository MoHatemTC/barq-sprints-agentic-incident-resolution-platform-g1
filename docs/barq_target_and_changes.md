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

**One incident page (ServiceNow)** — one page, no tabs, minimum fields.
- *Engineer:* the essentials, one AI panel (status, who is in control, urgency and why, the
  instructions, the history) and only the buttons that apply now.
- *Caller (portal):* their problem, the status, and the conversation with the AI or the
  engineer. Nothing technical.
- The exact fields are decided together field by field before anything changes.

**BARQ AI page (ServiceNow portal) — for callers only.** One chat page where a user raises a
problem, gets instructions, asks questions, follows their tickets, and reopens or closes them.
It remembers past conversations. Actions on tickets run in ServiceNow with that user's own
permissions, never the agent's. Engineers have no separate console: they work from the
incident page and lists.

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

1. **Finish the current core:** failure-hook tests, deploy #213 head, all live scenarios pass,
   rollback rehearsed once.
2. **Flexible contracts:** versioned events and agent actions, ServiceNow rule sends the new
   events, old flow still passes.
3. **Agent conversation and control (the connection core, section 2A):** every change becomes
   an event with its actor; control states; caller replies and ticket edits continue the AI;
   engineer edits take control; *Run AI again* / hand back; reopen → engineer; loop and
   self-trigger guards. Proven live with the caller and engineer acting on the same ticket.
4. **Agent intelligence:** urgency triage, look-around, outage and security handling, outcome
   recording.
5. **The incident page:** fields agreed with Ali, then built in the SDK app and installed.
6. **The BARQ AI page for callers:** chatbot moved behind the bridge with user identity,
   ticket actions, portal page; a ticket's chat is its comment thread, so engineer and AI
   messages appear in it and caller messages appear on the incident.
7. **Improvement loop and admin rules:** article proposals and scores, rules editable by an
   admin.
8. **Clean-up and docs;** #213 ready for Ali's approval.

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
