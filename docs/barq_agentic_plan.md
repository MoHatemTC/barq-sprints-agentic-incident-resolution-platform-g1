# BARQ agentic platform — the plan

Written 2026-10-03 07:15 Cairo. This is the single plan to execute. The design document
(`barq_agentic_platform_design.md`) stays as the reference for behaviour and scenarios; this
file says what we build, in what order, and when each step is done.

## Goal

An agent that works incidents in ServiceNow by itself and decides what to do — resolve, ask the
caller, hand over, escalate, open a parent incident for an outage — while engineers can see
everything, step in at any time and hand back. The caller always sees who is talking.

## Where we are (checked 2026-10-03)

- **One PR: #213** (`feat/admin-chatbot`): Kerolos's chatbot + all our work. Draft; Ali approves
  it at the end. #214 and #215 are closed into it. All CI checks pass, including the SDK audit
  (the unfixable `braces` advisory is accepted until 2026-10-31, nothing else).
- **Built and live-tested on the shared system:** the agent reads the incident, classifies,
  finds knowledge, writes a cited fix, checks it, assigns the group, sets In Progress, writes to
  the caller, and resolves as *BARQ AI Agent*. P1 and risky incidents park for an engineer, who
  approves, rejects or takes over from buttons on the incident form. Human Lock blocks the agent
  (ACLs, proven live). Kill switch and autonomy level exist. Backups and a rollback script exist.
- **Live result:** 5 of 6 core scenarios pass. The failing one (P1 reject, INC0010252) was a NUL
  character bug, fixed in code but not yet deployed.
- **Deployed on the EC2:** an older commit (`493836c`). The newer fixes are not on it yet.
- **Unfinished:** the failure hook (code pushed, tests missing).

## What is missing, and why

1. **The agent cannot decide.** Every run follows the same steps and ends in one fixed outcome.
   It cannot choose to ask the caller, hand over, or open a parent incident.
2. **It cannot see around the incident.** It never looks at similar open incidents or the
   caller's other tickets, so it cannot spot an outage or a repeat problem.
3. **It only hears new incidents.** ServiceNow does not tell it when the caller replies, reopens,
   or an engineer comments, so it cannot hold a conversation or react to a human.
4. **Engineers cannot hand back,** and the form does not show clearly who is in control.

## How we build it (no rewrite)

The 11-step pipeline stays: its safety gates are tested and proven live. We add two steps and a
few tools inside it, so the model decides *what* to do and the existing code decides *whether it
is allowed*:

- **investigate** (new step, read-only): similar open incidents and the caller's recent
  incidents, recorded as evidence.
- **decide** (inside `act`): the model picks one action — `resolve`, `ask_caller`, `hand_over`,
  `escalate`, `open_parent_incident` — with a reason. Code rules check it against risk, autonomy
  level, Human Lock, caller presence and evidence; anything not allowed becomes `escalate`.

## The steps

Each step ends deployed to the shared EC2, proven live on `dev407364`, committed to #213, with
evidence in `docs/evidence/`. Gate before every push: ruff, format, mypy, pytest.

### Step 1 — Finish and prove what exists
- Failure hook tests (released when nothing can be approved; kept when a real pause exists;
  kept when the check fails) + a PostgreSQL test of the query.
- Deploy #213 head to the EC2; run the 6 core scenarios + the caller-message scenario.
- Rehearse the rollback once.

**Done when:** all 7 live scenarios pass and the rollback has worked once.

### Step 2 — The agent hears ServiceNow
- ServiceNow business rule on incident updates sends three new events through the existing
  webhook: `incident.commented` (new customer-visible comment, with who wrote it: caller,
  engineer, agent), `incident.reopened`, `incident.closed`.
- Worker handles them without re-running the whole pipeline:
  - caller comment while the agent is waiting for the caller → resume the agent with the reply;
  - engineer comment while the agent is in control → agent stands down (`handed_over`), work note;
  - reopen of an AI resolution → hand over to the group with the agent's summary;
  - close → record the AI resolution as confirmed.
- `handed_over` processing state; eligibility refuses it.

**Done when:** live — caller reply reaches the agent; engineer comment stops it; reopen hands over.
Scenarios S2, S5, S6, S12, S15, D2–D4.

### Step 3 — The agent investigates and decides
- `investigate` step + two read tools: `find_similar_open_incidents`, `caller_recent_incidents`.
- `decide` in `act` with the five actions and the code rules.
- New tools: `ask_caller` (comment + On Hold – Awaiting Caller), `hand_over` (assign group +
  hand-over note + `handed_over`), `open_parent_incident` (create a parent incident and link the
  similar ones as children; needs a create ACL for the agent role — one update-set change).
- P1 reassessment with the hard rules of design 6.3: lowered only with no security / outage /
  Tier-1 / multi-caller signal and a second model check agreeing; always noted on the incident.

**Done when:** live — vague ticket gets one question and continues on the reply; fake P1 is
resolved with a reassessment note; five similar tickets produce one parent incident with the
others linked; "I want a person" hands over. Scenarios R1–R9, S7, F1.

### Step 4 — Engineers in control, visibly
- *Hand back to BARQ AI* action with an optional instruction; agent resumes with history and the
  instruction and tells the caller it is continuing on the engineer's behalf.
- Incident form: a *BARQ AI* section showing who is in control, what the agent did and why,
  confidence, and the actions. One *BARQ AI* menu: Needs me, Resolved by AI, Handed over, Failed.

**Done when:** live — take over, hand back with an instruction, undo, all visible on the form.
Scenarios S1, S3, S4, S8–S11, S13, S14, S16.

### Step 5 — Finish
Docs (design status, operations notes, evidence), PR description, #213 marked ready for Ali.
Always done, even if an earlier step is cut short.

## Not in this round

Engineer chat inside ServiceNow and the console page (Streamlit removal), knowledge scoring from
confirmations and reopens, Problem records, recent changes / CI lookups, screenshots, automatic
remediation, instance clean-up. The design document keeps them for the next round.

## Rules that do not change

Shared systems only (`dev407364` + shared EC2). The agent never uses an admin account; admin is
only for ServiceNow setup and labelled test incidents. Nothing is deleted on the instance. Every
ServiceNow change goes in the tracked update set with its previous value in the manifest. No
force-push. No attribution. No test or check is skipped.
