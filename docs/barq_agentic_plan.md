# BARQ agentic platform — the plan

Written 2026-10-03 07:30 Cairo. This is the single plan to execute. Behaviour, screens and
scenarios are specified in `barq_agentic_platform_design.md` (section numbers below point there).

## What Ali asked for, and where each item is built

| Ali's requirement | Step |
|---|---|
| Fully agentic: the agent works the incident itself; a human only where we choose | 1, 3, 4 |
| Very intelligent: understands the situation, talks naturally, acts on what it sees, judges whether a "critical" ticket really is critical — human kept | 4, 10 |
| Opens, closes and creates tickets (parent incident for an outage, split, link, close) | 4, 5 |
| Caller loop: AI resolves → caller confirms or stays silent → resolved by AI; caller reopens → engineer | 5 |
| Engineers steer: take over, hand back, edit, approve; the caller always sees who is talking | 3 |
| UI usable immediately (not localhost), inside ServiceNow, engineers see everyone's and the agent's work, approvals there | 2, 6 |
| Incident form redesigned (today it looks bad) | 2 |
| Chatbot from #213 continued, Streamlit removed, memory shared with the agent | 7 |
| New knowledge, and who can add it | 8 |
| The agent actually fixes things, like an engineer would | 9 |
| ServiceNow cleaned: nothing generic or unneeded | 2, 11 |
| Every scenario covered; everything reversible; tested without pushing to `main`; documented | every step, 11 |

## Where we are (checked 2026-10-03)

- **One PR: #213** (`feat/admin-chatbot`) = Kerolos's chatbot + all our work. Draft; Ali approves
  it at the end. All CI checks pass, including the SDK audit.
- **Built and live-tested:** the agent finds the fix, assigns the group, sets In Progress, writes
  to the caller and resolves as *BARQ AI Agent*. Risky incidents wait for an engineer, who uses
  Approve / Reject / Take over on the incident form (through the `BarqBackend` bridge). Human
  Lock blocks the agent. Autonomy level and kill switch. Backups and rollback script.
- **Live result:** 5 of 6 core scenarios pass; the sixth failed on a bug fixed in code, not yet
  deployed. The EC2 still runs an older commit (`493836c`).
- **Unfinished:** failure-hook tests.

## How every step is done

Each step is finished before the next starts: unit tests and the full gate (ruff, format,
mypy, pytest), CI green on #213, hand deploy to the shared EC2, live proof on `dev407364` with
evidence in `docs/evidence/`. ServiceNow changes go in the tracked update set with previous
values in the manifest; nothing is deleted. The order puts what engineers see early, so
whatever point we reach is a working product.

**Technical approach (no rewrite).** The 11-step pipeline and its proven safety gates stay. The
agent gains an *investigate* step (read-only look-around) and a *decide* step (the model picks an
action; code checks it is allowed). The ServiceNow screens are scoped UI pages and form views
that call the backend server-side through `BarqBackend`, because the EC2 is plain HTTP.

## The steps

### 1. Finish and prove the core
Failure-hook tests (+ PostgreSQL test) → deploy #213 head → 6 core scenarios + caller-message
scenario live → rollback rehearsed once.

### 2. The incident form and ServiceNow cleaned up (design 11.1–11.3, 12)
- New *BARQ AI* form view: the **AI card** at the top (status chip, who is in control,
  confidence, the fix as steps with article links, why, buttons for this user), the standard
  fields below, an **AI timeline** tab, an **AI diagnostics** tab (admins) holding the technical
  fields that clutter today's form.
- Incident list: **AI status** column with colours; filters *Needs my approval*, *Resolved by AI —
  waiting*, *Reopened from AI*, *AI failed*.
- One **BARQ AI** menu replacing the three scattered entries; old buttons and menus deactivated.

### 3. The agent hears ServiceNow; engineers in control (design 6.4)
- New events through the existing webhook: caller comment, engineer comment, reopen, close.
- Control rules: engineer comment → agent stands down; **Hand back to BARQ AI** with an
  instruction → agent continues with the history and says so to the caller; **Undo**; drafts for
  an engineer stay private; caller asks for a person → hand over.
- Scenarios S1–S16.

### 4. The agent investigates and decides (design 6.3)
- *investigate*: similar open incidents, the caller's recent incidents.
- *decide*: resolve · ask the caller · hand over · escalate · **create** a parent incident and
  link the similar ones · **split** a ticket with two problems · **close** a duplicate.
- P1 reassessment under the hard rules (no security / outage / Tier-1 / multi-caller signal, a
  second model check agrees, always noted, Undo available); outages raised.
- Scenarios R1–R9, F1–F5, C1–C6.

### 5. Caller loop (design 6.2, D)
Clarifying question → On Hold – Awaiting Caller → reply resumes; confirmation window job
(30 min for tests): silence or "it works" → confirmed and closed as resolved by AI; reopen →
engineer with the agent's summary; article credited or penalised. Scenarios D1–D8, L1.

### 6. BARQ AI Console inside ServiceNow (design 11.4)
One page at the instance URL: **Live** (what the agent is doing now), **Needs me** (approvals and
reopened incidents for my groups), **Results** (resolved, confirmed, reopened, escalated, failed,
time to resolve, best and worst articles), **Failed & sync** (retry; ServiceNow vs backend
mismatch report), **Settings** (autonomy, window, category → group, kill switch, bridge health).
Scenarios E, G7, I1–I4.

### 7. Chat with memory inside the console (design 10, H)
#213's chat graph behind the bridge; each engineer identified by their ServiceNow login; answers
from knowledge, and about any incident from the agent's timeline; proposes actions (work note,
assign, hand back) that run only on Confirm, through the same tools and audit; shows who said
what; shared memory with the agent (incident, engineer, team); Streamlit removed.
Scenarios H1–H8.

### 8. Knowledge lifecycle (design 9)
Engineer's fix → knowledge proposal → knowledge approver publishes → indexed; articles written in
ServiceNow indexed on publish, removed on retire; only the approver role publishes.
Scenarios B1–B7.

### 9. The agent fixes things (design 7A, fulfil through ServiceNow)
Runbook catalog; the agent orders a catalog item, raises a standard change from a pre-approved
template, creates an incident task, or unlocks the caller's account, links it, and resolves when
it completes. Scenarios K1–K4, K6–K10.

### 10. The agent sees attachments
Screenshots and log files on the incident read through the model's vision input, redacted and
screened, quoted as evidence.

### 11. Clean-up, docs, ready for approval (design 12, 14, 15)
Test data archived per Ali's decisions (never deleted), sync report clean, docs to the full app
(operations, screens, scenarios, evidence), rollback documented, #213 ready for Ali.

## Rules that do not change

Shared systems only (`dev407364` + shared EC2). The agent never uses an admin account; admin is
only for ServiceNow setup and labelled test incidents. Nothing deleted on the instance. No
force-push. No attribution. No test or check is skipped.
