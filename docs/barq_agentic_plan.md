# BARQ agentic platform — the plan

Rewritten 2026-10-03 07:10 Cairo after Ali's review ("the plan is not right, and the system is
not ready for all that is needed"). This file is the single plan. Behaviour, screens and the
full scenario catalogue stay in `barq_agentic_platform_design.md` (section numbers point there).

## 1. The end system, as software

**What a person sees.**
- *Caller* (ServiceNow portal): raises an incident. Within minutes a message from **BARQ AI
  Agent** either gives steps they can do themselves, asks one question, or says an engineer from
  the named group has it. After a self-service fix they press *It works* (or stay silent) and the
  incident closes as resolved by AI; *Reopen* sends it to an engineer with the agent's summary.
  The caller always sees who is writing: the agent or a named engineer.
- *Engineer* (incident form, *BARQ AI* view): an **AI card** at the top showing status, who is in
  control (agent / waiting for approval / engineer), how urgent the agent judged it and why, the
  fix with article links, and buttons: **Approve**, **Reject**, **Take over**, **Hand back to
  BARQ AI** (with an instruction), **Undo**. Lists filtered to *Needs my approval*, *Reopened from
  AI*, *AI reassessed*, *AI failed*.
- *Team lead / admin*: the **BARQ AI Console** page in ServiceNow (live work, results, failures,
  settings: autonomy level, kill switch, confirmation window) and a chat that answers about
  knowledge and any incident, with shared memory.

**How it works underneath (one loop per incident event).**

```
ServiceNow incident change ─► Business Rule decides "does the agent need to look?"
   (created, caller replied, engineer handed back, reopened, closed)
        │ minimal event (no incident text)
        ▼
Webhook 202 ─► Redis/Celery worker ─► LangGraph run
   1 load + re-read        5 retrieve knowledge
   2 validate / control    6 diagnose + draft + verify citations
   3 TRIAGE (new)          7 safety + confidence gates
   4 INVESTIGATE (new)     8 DECIDE + ACT through registered tools only
        │                         (assign, comment, ask, resolve, hand over, escalate,
        │                          link/parent incident; park for approval)
        ▼
ServiceNow updated as BARQ AI Agent ─► PostgreSQL audit + Langfuse trace
Engineer buttons ─► BarqBackend (server-side) ─► backend approve / reject / hand back
```

Code decides what is *allowed*; the model decides what is *best* among allowed actions. The
model never gets a tool that bypasses the registry, never sees admin credentials, and every
write is re-checked against the live incident (Human Lock, state, owner) first.

## 2. What is wrong or missing today (checked in the code, 2026-10-03)

| # | Gap | Why it matters | Fixed in |
|---|---|---|---|
| G1 | The ServiceNow trigger only fires on changes to `active`, `category`, AI Enabled, Human Lock, and only while AI state is `pending`/`failed`. Caller replies, reopen, close and "hand back" fire **nothing**. | The agent cannot hear the caller, cannot be called back, cannot learn a reopen. | Step 2 |
| G2 | Eligibility refuses anything not `pending`. Once processed, an incident can never go back to the agent. | An engineer cannot "re-add the AI". | Step 2 |
| G3 | Backend event types are only `incident.created` / `incident.updated` (DB constraint). | New events need a migration and handlers. | Step 2 |
| G4 | Urgency = priority matrix + category only. A P4 "whole floor offline" is low; a P1 "forgot password" is high. | Wrong handling both ways. | Step 3 |
| G5 | Security handling = injection screening + "security category → park". No path for a *reported* attack (phishing, ransomware, compromised account) and no protection against ticket floods. | An attack could be "self-served" or the agent flooded. | Step 3 |
| G6 | `mark_incident_failed` left incidents stuck at "awaiting approval" with nothing to approve (INC0010252). | Engineers see work nobody can do. | Step 1 (code done, tests pending) |
| G7 | The incident form shows raw AI fields; no AI card, no control state, no list filters. | Engineers cannot see what the agent is doing. | Step 4 |
| G8 | One shared operator identity for every engineer action. | Audit says "operator", not who. Mitigated: the bridge passes the engineer's ServiceNow name with every decision. | Step 2 |
| G9 | One 2-vCPU / 3.8 GB EC2, first answer up to ~150 s under a burst, OOM seen at concurrency 4. | Live demos must be sequential; concurrency stays 2. | constraint |
| G10 | EC2 is plain HTTP on a bare IP. | Every UI call must go server-side through ServiceNow (`BarqBackend`); no browser calls. | constraint |
| G11 | Two Claude sessions wrote to this same branch on 2026-10-03. | Conflicting plans and edits. | one session only |

## 3. The behaviours Ali asked about

**How the agent decides how urgent a ticket really is (Step 3).** A new *triage* step produces
one of **Critical · Urgent · Normal · Self-service**, with reasons, from:
- the record: priority/impact/urgency matrix, category, the CI and whether it is a Tier-1 service,
  caller VIP flag;
- the text (model + keyword floor): outage words ("nobody", "whole office", "down"), data-loss
  and security words, deadlines, number of people affected;
- the system: similar incidents opened in the last 30–60 minutes (a burst means an outage), the
  caller's own recent tickets (repeat = previous fix failed).

Rules in code: triage may always **raise**; it may **lower** a P1/P2 only if none of the hard
signals is present (security, data loss, outage words, Tier-1 CI, VIP, a burst of similar
tickets) **and** a second independent model check agrees. A lowered ticket is flagged *AI
reassessed* with the reasons, the priority field is never changed silently, and an engineer can
**Undo**. Scenarios R1–R9.

**A ticket that is not urgent and the caller can fix (Step 3 + 5).** Triage = Self-service and the
caller-message check confirms every step is doable without IT rights → the agent posts numbered
steps, resolves, and the confirmation window runs: *It works* or silence → closed as resolved by
AI; *Reopen* → engineer. If the fix needs IT rights, the agent assigns the group and writes the
fix privately for the engineer instead (already live).

**An engineer reopens or wants the AI back (Step 2).** New *Hand back to BARQ AI* button (optional
instruction). It records who and why, sets the AI state back to `pending`, and the Business Rule
sends `incident.handed_back`. The agent runs again with the whole history (caller replies,
engineer notes, the instruction) as evidence, never repeats a question already asked, and tells
the caller it is continuing on the engineer's behalf. Loop guard: at most two hand-backs per
incident; a second failure goes straight back to the engineer. A *caller* reopen of an AI
resolution never re-runs the agent automatically: it goes to the engineer with the summary, and
the article that led to it is penalised. Scenarios S1–S16 (design 6.4).

**What happens if it is an attack (Step 3).** Three different cases:
1. *The ticket reports an attack* (phishing email clicked, ransomware note, account used by
   someone else, data leak): triage = Critical + security. The agent never gives self-service
   steps, never tells the caller to delete or reinstall anything (evidence must be preserved),
   assigns the security group, parks for a human, and writes a private brief (what was reported,
   indicators found in the text, similar recent reports). A burst of similar reports raises an
   outage-style parent incident.
2. *The ticket is the attack* (prompt injection, instructions to the AI, links, encoded text):
   hard screening blocks it before any model sees it, nothing from the model is written, the
   incident is flagged for review with the reason (already live, scenario C5/INC0010253); the
   agent has no tool that could leak data or raise privileges, and never uses admin credentials.
3. *Abuse of the agent itself* (ticket flood, one caller opening many tickets, impersonation):
   per-caller and global rate limits on agent runs, bursts grouped instead of processed one by
   one, the kill switch on the console, and the agent only acts on the caller recorded on the
   incident, never on a name written in the text.

## 4. Order and scope — what fits and what does not

Ordering rule: each step needs the one before it, and each ends **deployed to the shared EC2,
tested live on `dev407364`, with evidence**, so wherever we stop the system works. Realistic
reading: by the 12:00 deadline (about 5 hours from 07:00) Steps 1–3 are realistic and Step 4 is
possible; Steps 5–7 are the next round. Everything is still built; nothing is dropped.

| Step | What | Closes gaps | Done when (live on the shared systems) |
|---|---|---|---|
| **1. Prove the core** | failure-hook tests; deploy #213 head; 6 core scenarios + caller-message scenario; rollback rehearsed once | G6 | 7/7 pass, evidence committed |
| **2. The agent hears ServiceNow; engineers in control** | Business Rule sends caller reply / hand back / reopen / close events (relevant-field + journal change); migration widens event types; worker handlers; *Hand back to BARQ AI* + *Undo* buttons; loop guard; control state | G1 G2 G3 G8 | S1–S16 pass: take over, hand back with instruction, caller reopen → engineer |
| **3. Triage, investigation and attacks** | triage step (4 classes, reasons, raise/lower rules, second check, *AI reassessed* flag); investigation (similar recent incidents, caller history, CI); security path; rate limits | G4 G5 | R1–R9 + attack cases 1–3 pass; zero wrong downgrades |
| **4. Engineers see it** | *BARQ AI* form view with the AI card; list column and filters; one BARQ AI menu; old buttons retired | G7 | an engineer can run every step-2/3 scenario from the form |
| **5. Caller conversation** | clarifying question → On Hold – Awaiting Caller → reply resumes; confirmation window job; *It works* / silence → closed as resolved by AI; article credit/penalty | — | D1–D8, L1 |
| **6. Console and chat in ServiceNow** | console page (Live, Needs me, Results, Failed & sync, Settings); #213 chat behind the bridge with memory per incident/engineer/team; Streamlit removed | — | E, G7, H1–H8, I1–I4 |
| **7. Knowledge, outages, fixing, clean-up** | knowledge proposals and publish/retire sync; parent incident/Problem for bursts; remediation (catalog item, standard change, account unlock); attachments read with vision; instance clean-up; final docs; #213 ready for Ali | — | B1–B7, F1–F5, K1–K10, J |

Why this order: Step 2 is the foundation for everything conversational (caller loop, hand back,
reopen all depend on the agent hearing events). Step 3 is what makes the agent intelligent and
safe, and it must exist before more autonomy is shown. Step 4 makes Steps 2–3 visible. The
console, chat and remediation build on all of them.

## 5. How every step is tested

Unit tests and the full gate (ruff, format, mypy, pytest) → CI on #213 → hand deploy to the
shared EC2 (backup taken; rollback script ready) → labelled `[BARQ-TEST-…]` incidents on
`dev407364` created with the admin login (setup only; the agent works as *BARQ AI Agent*) →
read-back of ServiceNow, PostgreSQL rows and the Langfuse trace → evidence in `docs/evidence/`.
ServiceNow changes go into the tracked update set with previous values in the manifest; nothing
is deleted on the instance. No test or check is skipped.

## 6. Rules that do not change

Shared systems only (`dev407364` + shared EC2). One PR (#213), approved by Ali at the end. The
agent never uses an admin account. No force-push. No attribution. One working session at a time.
