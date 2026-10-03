# BARQ Agentic Incident Platform — Target Design

Status: **proposal for review** (2026-10-03). Nothing in this document is built yet unless a
section says so. It describes the system we intend to have after cleaning, fixing and
extending the current platform, every scenario it must handle, and how each change can be
undone.

Facts about the current system were checked live on 2026-10-03 against the shared
ServiceNow instance `dev407364`, the shared EC2 backend and this repository (branch base:
PR #213 head `d7548ea`, which is `main` `e065df9` plus the admin chatbot).

---

## 1. What we are building, in one paragraph

An incident platform where the AI agent does the work and people stay in control of what
matters. When an incident is raised, the agent triages it, assigns it, works out a fix from
the knowledge base, and — when the incident is low-risk and the evidence is strong — gives the
fix to the caller and resolves the incident itself. The caller is the human check for that
case: if they come back and say it did not work, the agent hands the incident to an engineer
with everything it tried; if they stay silent through a confirmation window, the resolution is
confirmed and credited to the AI. Anything high-risk, uncertain or new waits for an engineer,
who decides from inside ServiceNow. Everything the agent does is visible on the incident, in a
console inside ServiceNow, and in the audit trail, and the agent and the chatbot share one
memory so that what is learnt on one incident helps the next one.

## 2. Principles

1. **Autonomous by default, human where it matters.** The agent acts alone on reversible,
   low-risk work. Irreversible or high-risk actions always need a recorded human decision
   (PRD FR-17, NFR-05).
2. **Visible always.** Every agent action appears on the incident (activity stream, AI card,
   timeline) and in the console, with its reason and its evidence.
3. **ServiceNow is where people work.** Engineers, callers and admins never use a separate
   website, a localhost page, or a pasted token.
4. **One source of truth per fact.** ServiceNow owns the incident; PostgreSQL owns the agent's
   execution, decisions and memory; Qdrant holds only vectors; settings live in one place.
5. **Nothing generic we do not need.** No email (it is switched off on the instance), no
   Virtual Agent, no workspace customisation, no new instance-wide settings, no Streamlit.
6. **Every change is reversible** (section 14).
7. **No admin credential in the running system** (PRD FR-06, A-05). Admin access is used only
   for setup, inside a tracked update set.

## 3. Who uses it and what each can do

| Actor | ServiceNow identity / role | Can do | Cannot do |
|---|---|---|---|
| **Caller** (employee with a problem) | any user; uses the portal (`/sp` or Employee Center `/esc`) | raise an incident; read the AI's fix on their ticket; reply / reopen; accept the resolution | see the console, approvals, AI internals |
| **Engineer** (service desk / resolver) | `itil` + `x_2215032_ai_inc_0.user` (view) | see the AI card, timeline and console; chat with the assistant; take over an incident | approve AI actions (needs approver role) |
| **Approver** (senior engineer / team lead) | `x_2215032_ai_inc_0.operator` | everything an engineer can, plus approve / edit & approve / reject paused runs; retry failed runs; release a lock so the AI may retry | change settings |
| **Knowledge approver** | `x_2215032_ai_inc_0.kb_publisher` (exists) | approve a human fix becoming a published knowledge article; retire articles flagged as failing | — |
| **BARQ admin** | `x_2215032_ai_inc_0.admin` | change autonomy level, confirmation window, category → group mapping, kill switch; see health and sync reports; grant the roles above | — |
| **AI agent** | `ai_orchestrator_svc`, display name **BARQ AI Agent**, role `integration_writer` (OAuth, least privilege) | only the registered tools in section 7, only through ServiceNow ACLs scoped to them | anything not registered; anything on a human-locked incident |
| **KB publisher service** | `kb_publisher` | publish/retire knowledge articles the human decided on | incidents |

Who grants roles: only a ServiceNow admin (today: Mohamed / Aya hold the admin login). The
console's Settings page shows who holds each role so it is never guesswork.

## 4. Architecture after the change

```
Caller ─► Portal ─► Incident (ServiceNow)
                        │  Business Rule (eligibility) ─► minimal event (event_id, sys_id, number, type)
                        ▼
               FastAPI webhook (202, no model) ─► Redis/Celery ─► LangGraph incident agent
                        ▲                                   │  tools via registry (ACL-scoped)
                        │                                   ▼
   BARQ AI Console (React UI Page in our scope) ◄─ Scripted REST (server-side bridge) ─► backend API
   AI card + timeline on the incident form           (runs as the logged-in engineer,
   UI Actions (approve / reject / take over / retry)   calls the backend with the operator
                                                       credential from one property and
                                                       passes the engineer's identity)
   PostgreSQL: executions, decisions, memory  ·  Qdrant: knowledge vectors  ·  Langfuse: traces
```

Why a server-side bridge: the backend is plain HTTP on a bare IP and ServiceNow is HTTPS, so a
browser on ServiceNow cannot call it directly (mixed content). ServiceNow calls it from its
servers instead — the pattern the existing DLQ page already uses — and the engineer's real
ServiceNow identity is attached to every decision.

Removed from the design: the Streamlit chat UI; the backend `/review` page as an engineer tool
(kept only as an internal fallback); hard-coded secrets in ServiceNow scripts.

## 5. The incident lifecycle

### 5.1 ServiceNow incident states (unchanged, out of the box)
New (1) → In Progress (2) → On Hold (3) → Resolved (6) → Closed (7), or Canceled (8).
Out-of-the-box behaviour we rely on (verified on the instance):
- `mark_resolved` fills **Resolved by** with whoever resolved it → the agent shows as
  *BARQ AI Agent*.
- `incident reopen` + `Reopen Count` + `Clear Resolve fields`: a caller comment on a resolved
  incident reopens it, increments `reopen_count` and clears the resolution.
- `incident autoclose`: resolved incidents close after `glide.ui.autoclose.time` = **7 days**
  (instance-wide; we do not change it).

### 5.2 AI processing states (our field `ai_processing_state`)
Current values: pending, in_progress, awaiting_approval, complete, failed. Target meaning, shown
on the AI card as a plain-language status chip:

| Chip | Meaning | ai_processing_state |
|---|---|---|
| Queued | event received, waiting for a worker | pending |
| Working | agent running | in_progress |
| Needs engineer approval | paused for an approver | awaiting_approval |
| Resolved by AI — waiting for caller | resolved, inside the confirmation window | complete + `ai_outcome=awaiting_caller` |
| AI resolution confirmed | window passed, caller did not reopen (or accepted) | complete + `ai_outcome=confirmed` |
| Reopened — now with an engineer | caller reopened; agent handed over and locked itself out | complete + `ai_outcome=reopened` + human lock |
| Suggested — engineer to apply | fix written but not applied (Suggest-only level, or caller unknown) | complete + `ai_outcome=suggested` |
| Escalated | risk / no evidence / blocked; engineer owns it | awaiting_approval or failed |
| Failed — can retry | run failed after retries | failed |

`ai_outcome` is one new choice field in our scope (section 13). Existing state values are not
changed, so the S1.3 eligibility rule and older data keep working.

## 6. Autonomy and decision policy

### 6.1 Autonomy level (one setting, console → Settings, admin only)

| Level | Agent does alone | Needs a human |
|---|---|---|
| Suggest only | classify, work note with the cited fix | everything else |
| Assist | + assign group, set In Progress, link article | caller comment, resolve, publish knowledge, any high risk |
| **Autonomous (target default)** | + caller comment with the fix, resolve, confirm-close after the window, create/link Problem, split, link duplicate | high-risk incidents, low confidence, no evidence, guardrail blocks, publishing knowledge, cancelling a ticket |
| Kill switch (off) | nothing; events are accepted and recorded but not processed | — |

### 6.2 Decision table (Autonomous level)

| Risk (deterministic, before retrieval) | Evidence & confidence | Caller known? | Agent action |
|---|---|---|---|
| HIGH (P1, unknown priority, security) | not computed | any | park → engineer approval; nothing written except the "needs approval" note |
| ELEVATED (Tier-1 service, MFA/authenticator) | draft produced | any | park → approval of the draft; on approval the fix is applied as below |
| LOW | strong evidence, verified citations, confidence ≥ floor | yes | caller comment with the fix → Resolved by BARQ AI Agent → confirmation window |
| LOW | as above | **no caller / caller is a service account** | write the fix as *Suggested*, assign group; an engineer applies it (nobody can confirm it) |
| LOW | confidence below floor, or verification failed after revisions | any | park → engineer approval of the draft |
| any | no relevant knowledge ("new kind of incident") | any | park → engineer writes the fix → becomes a knowledge-article proposal (section 9) |
| any | guardrail block (injection, unsafe output) | any | park with the block reason; nothing from the model is written |

The first risk verdict is deterministic and is never lowered by retrieval or model output.
Section 6.3 adds one controlled exception: a reassessment step that can lower how a HIGH
incident is *handled* (never its ServiceNow priority), only on evidence, under hard rules
enforced in code, and always visible to an engineer, who can undo it.

### 6.3 An intelligent agent with the human kept (Ali, 2026-10-03)

Ali's direction: the agent should be very intelligent and autonomous — understand the whole
situation, talk naturally, see what is attached, act on what it sees, and recognise that a
"critical" ticket may not be critical — while a human stays in the loop. Ali chose that a P1
the agent judges to be low risk is handled like any low-risk incident (option a), with the
human kept through the safeguards below.

**Investigate before acting (T16).** Inside the fixed pipeline's gates the agent gets a
bounded loop — observe, reason, call a READ tool, observe again — over: the incident and its
journal, the caller's recent incidents, similar open incidents (a possible wider outage), the
affected CI/service, recent changes, attachments and knowledge. It stops when it has enough
evidence or after a fixed step budget, and every observation it relies on is recorded as
evidence. It never gains write tools this way: writes still go through `act` and the registry.

**Reassess risk in both directions (T17).** After investigating, the agent may propose a
different handling class, with the evidence:
- *Lower* (e.g. a P1 "can't log in" that is one user and a password problem). Allowed only if
  all of these hold, checked in code: no security, data-loss or outage signal in the text or
  investigation; no other open incident with the same symptom; the CI is not a critical/Tier-1
  service; the caller is not flagged VIP; a second, independent model check agrees; and the
  reassessment setting is on. Then the incident follows the LOW row of 6.2.
- *Higher* (e.g. a "minor" ticket matching five others → likely outage): always allowed; the
  agent parks it for an engineer and proposes a Problem (T11).
- The ServiceNow priority field is never changed silently. The agent writes a work note
  "Handled as low risk because …" with the evidence, the incident is flagged *AI reassessed*,
  and it appears in the console's *Needs me* view for the confirmation window, where an engineer
  can *Undo* (reopen, hand over, lock) with one click.

**How the human stays in the loop.** Engineers see every reassessment and can undo it; the
caller confirms or reopens every resolution (T6); P1 parks still need an engineer whenever any
rule above fails; the kill switch and the reassessment switch are admin settings; and the
intelligence test set (T21) must show zero wrong downgrades before the switch is on by default.

**Talk naturally (T18).** With the caller, in incident comments: ask one clarifying question
when information is missing (incident On Hold – Awaiting Caller), read the reply, continue,
and confirm the fix worked — this is the conversation behind T6. With engineers, in the
console chat (T9): explain decisions, accept instructions ("try X", "take this one") as
proposals that go through the same gates. Agent and chat share the memory of section 10.

**See (T19).** Screenshots and log files attached to an incident are read through the
current model's vision input (via the LiteLLM proxy), redacted and screened like any input,
and quoted as evidence.

**Learn from outcomes (T20).** Reopens count against the article and the decision path that
led to them, confirmations count for them, and past outcomes for similar incidents feed the
confidence score.

## 7. Agent abilities (registered tools)

Every tool is registered with a server-owned permission class, typed arguments, an audit row
before dispatch, and a ServiceNow ACL that allows exactly that write and nothing else. HIGH_RISK
tools run only when the latest approval row for that execution and tool is `approved`.

| Tool | Class | What it writes | New ACL needed |
|---|---|---|---|
| read_incident | READ | — | no |
| search_similar_incidents | READ | — (bounded query, trusted filters) | read already granted |
| write_ai_fields | LOW_RISK_WRITE | AI fields | no |
| write_work_note | LOW_RISK_WRITE | work note | no |
| write_execution_log | LOW_RISK_WRITE | AI Execution Log row | no |
| assign_incident | LOW_RISK_WRITE | assignment_group (from category mapping) | yes |
| set_in_progress | LOW_RISK_WRITE | state New → In Progress | yes |
| comment_to_caller | LOW_RISK_WRITE at Autonomous / HIGH_RISK below | customer-visible comment | yes |
| resolve_incident | LOW_RISK_WRITE only when the decision table allows; else HIGH_RISK | state Resolved, close_code *Solution provided*, close_notes (fix + sources) | yes |
| confirm_close | LOW_RISK_WRITE | state Closed after the window, `ai_outcome=confirmed` | yes |
| hand_over_to_engineer | LOW_RISK_WRITE | human lock on, `ai_outcome=reopened`, work note brief | yes (lock field) |
| create_problem | LOW_RISK_WRITE (Autonomous) | new Problem; links incidents' problem_id | yes (problem create) |
| link_parent_incident | LOW_RISK_WRITE | parent_incident on followers | yes |
| split_incident | HIGH_RISK | new incident for a second issue, linked as related | yes (incident create) |
| mark_duplicate | HIGH_RISK | close as *Duplicate* of another incident | yes |
| publish_kb_article | HIGH_RISK (exists) | knowledge article via kb_publisher | no |
| run_runbook | per runbook (catalog) | a catalog request, standard/normal change, incident task, account unlock, or a lab runner action — see 7A | yes (request/change create; user unlock) |
| cancel_incident | not given to the agent | — | — |

Every ServiceNow write the agent makes appears in the incident's activity stream as **BARQ AI
Agent** and in the timeline with the reason.

## 7A. Remediation — the agent fixes the problem, not only the ticket

Today the agent writes a fix for a person to carry out. This section adds a **remediation
layer**: the agent carries out the fix itself, the way an engineer would, through a small,
allowlisted set of runbooks. The backend already advertises a disabled `auto_remediation`
feature flag; this is the design behind it.

### 7A.1 What the instance can execute (verified 2026-10-03)

| Capability on `dev407364` | What it gives the agent |
|---|---|
| Flow Designer + Flow Engine + Flow Trigger, scriptable flow API | run a named subflow from our scope with typed inputs and read its outputs |
| **ITSM Spoke** actions | Create Standard Change Request from Incident, Create Normal / Emergency Change from Incident, Create Problem from Incident, Create Request, Create Catalog Task, Create Incident Task, Update Assignment Group, Assign Incident to CI Support Group, Add Comment / Work Note |
| **Standard change catalog** (pre-approved templates) | Reboot Windows Server, Replace printer toner, Clear BGP sessions on a Cisco router, Change VLAN on a Cisco switchport, Add network switch to datacenter cabinet, Decommission local office Domain Controller |
| **Service Catalog** items | Install Software, Corp VPN, New Email Account, Request email alias, Temporary group membership, Group Modifications, Renew Certificate, Loaner Laptop, Standard Laptop, Firewall Rule Change, New virtual PC, Endpoint Security, … |
| **Password Reset** plugin + API (incl. *Service-Desk Password Reset for Local ServiceNow*) | identity actions on ServiceNow accounts (unlock, reset through the official process) |
| User records (`sys_user.locked_out`; 2 locked users today) | unlock a locked ServiceNow account |
| **No MID Server** | nothing on-premise or inside a corporate network can be reached (no Active Directory, PowerShell, SSH). Real infrastructure actions therefore need a reachable runner (7A.4) |

### 7A.2 Three kinds of fix

1. **Fulfil through ServiceNow (real, available now).** The agent orders the right catalog
   item for the caller (e.g. VPN access, software install, temporary group membership,
   certificate renewal, loaner laptop), raises a **standard change** from a pre-approved
   template (e.g. reboot a server, replace printer toner), creates an incident task for a
   field engineer, or unlocks the caller's ServiceNow account. The request or change is linked
   to the incident; when it completes, the incident is resolved as *Resolved by request* /
   *Resolved by change* and goes through the caller loop.
2. **Execute against infrastructure (needs a runner).** A remediation runner the backend can
   reach executes allowlisted runbooks (restart a service, clear a queue, flush a cache, renew
   a certificate) with pre-check, execution, verification and automatic rollback. On this
   project it targets a clearly labelled **lab environment**, because there is no real
   infrastructure or MID Server to reach.
3. **Guide the caller** (exists): numbered self-help steps on the ticket.

### 7A.3 How the agent decides — knowledge-driven, never invented

- Each runbook is declared once in a **runbook catalog** (our scope): name, description, the
  ServiceNow subflow or runner action it calls, typed inputs, which inputs must come from the
  incident (caller, CI, service), permission class, change requirement, verification check,
  rollback, rate limit.
- A knowledge article can name the runbook that applies ("Remediation: corp_vpn_access").
  **The agent may only propose a runbook that is linked to an article it cited** and whose
  inputs it can fill from the incident record. A model cannot invent an action, a target or a
  parameter.
- The runbook is called through one registered tool, `run_runbook(name, inputs)`, which
  checks the catalog, validates the inputs against the incident, applies the permission class
  and the autonomy level, and records everything before dispatch.

### 7A.4 Safety model for anything that changes a system

| Guard | Rule |
|---|---|
| Allowlist | only catalogued runbooks; unknown names refused and audited |
| Target binding | the target must be the incident's caller, CI or service — never a free-text target |
| Risk | P1, security and elevated incidents never run a runbook without approval; each runbook has its own class (e.g. order VPN access = low, reboot a server = needs approval) |
| Change control | infrastructure runbooks run only under a standard change (pre-approved template) or an approved normal change, created and linked by the agent |
| Pre-check → execute → verify | the runbook defines how success is checked; if the check fails the agent rolls back and hands over to an engineer |
| Idempotency | one execution key per (incident, runbook); a retry never runs it twice |
| Blast radius | one target per run; per-runbook rate limit; global kill switch |
| Identity | the agent runs as its own least-privilege user; flows run with that user's rights, never as admin |
| Audit | execution log row, change/request record, timeline entry, Langfuse span for every step |

### 7A.5 Remediation scenarios

- **K1 VPN access missing** → article names `corp_vpn_access` → agent orders *Corp VPN* for the
  caller (low risk) → request fulfilled → incident *Resolved by request* → caller loop.
- **K2 Software needed** → orders *Install Software* with the package from the article.
- **K3 Locked ServiceNow account** → unlocks the caller's own account (only the caller's,
  verified from the incident) → caller confirms.
- **K4 Printer out of toner** → standard change *Replace printer toner* → change task for
  facilities → resolved when the change closes.
- **K5 Server needs a reboot** → elevated → approval → standard change *Reboot Windows Server*
  → runner executes in the lab → verification → resolved.
- **K6 Verification fails** → automatic rollback → handed to an engineer with the run log.
- **K7 Runbook not linked to the cited article** → agent cannot run it; it proposes the steps
  as a draft instead.
- **K8 Same runbook requested twice** (retry, duplicate event) → second run refused by the
  idempotency key.
- **K9 Request or change rejected or fails in ServiceNow** → incident returns to the group with
  the reason.
- **K10 Kill switch on** → runbooks disabled; agent falls back to written fixes.

## 8. Scenario catalogue

Each scenario lists the trigger, what happens, what people see and where it ends. The ID is
reused in the test plan (section 15). "Card" = the AI Assistant card on the incident form.

### A. Intake
- **A1 New eligible incident** (active, AI Enabled, supported category, not locked). Event →
  202 in under 500 ms → worker → agent. Card: *Queued → Working*.
- **A2 Not eligible** (inactive, AI disabled, category not supported, already complete).
  No event, or the agent records *skipped* and writes nothing. Card shows the reason when AI
  Enabled is on but the category is unsupported.
- **A3 Human-locked incident.** No event; if the lock is set while the agent is mid-run, the
  final write is refused by the ACL and recorded as *skipped_human_lock*.
- **A4 Duplicate or replayed event.** Idempotency key → no second execution, no second write.
- **A5 Incident updated after processing** (description edited). Not re-triggered (the rule
  watches active, category, AI Enabled, Human Lock only). An engineer can press *Re-run AI*.
- **A6 Burst** (many incidents at once). Queued; at most N in parallel (memory-safe
  concurrency); similar ones cluster (F1).
- **A7 Incident with no caller or a service-account caller.** Fix is written as *Suggested*,
  never auto-resolved (D7).
- **A8 Incident raised by the agent itself** (split, F4). Born linked to its origin; runs
  through intake like any other incident.

### B. Knowledge
- **B1 Strong match.** Cited fix, confidence shown, proceeds per decision table.
- **B2 Weak match.** Low confidence → engineer approval of the draft.
- **B3 No match — a new kind of incident not in the knowledge base.** Agent parks with what it
  searched and the closest match. Engineer fixes it and writes the fix in the approval panel →
  a knowledge-article proposal is created → a knowledge approver publishes it → it is indexed →
  the next similar incident is resolved by the agent (end-to-end learning loop).
- **B4 Conflicting or outdated articles.** Only *published*, current versions are retrievable;
  retired versions are excluded (existing filters). The card lists the article and version used.
- **B5 Restricted article.** Excluded above the configured security level (existing).
- **B6 An article's fix fails** (caller reopened, D4). Article gets a failure count; ranking
  penalises it; after a threshold it is flagged on the console for the knowledge approver to
  review or retire.
- **B7 Engineer writes or edits an article directly in ServiceNow.** On publish, a business
  rule sends a minimal event → backend indexes it (event-driven, no polling). On retire, its
  vectors are removed. Today this does not happen: only the 50 seeded points are indexed while
  the instance has 126 published articles.
- **B8 Process articles** (60 of the 84 in the main base). Kept out of incident retrieval (they
  describe procedures, not fixes) but available to the chatbot.

### C. Risk and safety
- **C1 Priority 1.** Parks before any retrieval; approver decides — unless the reassessment of
  6.3 (T17) finds it low risk under every hard rule, in which case it is handled as low risk,
  flagged *AI reassessed*, and an engineer can undo it.
- **C2 Security category.** Same as C1.
- **C3 Elevated** (Tier-1 service, MFA reset). Draft produced, parks for approval.
- **C4 Priority raised after the agent resolved it.** The resolution stands but the card shows
  *risk changed — review*; approvers see it in *Needs me*.
- **C5 Prompt injection in the description.** Hard patterns block; softer ones go to a second
  check; blocked runs park with the reason; nothing model-generated is written.
- **C6 Secrets or personal data in the description** (including inside JSON). Redacted before
  any model call and never present in traces.
- **C7 Locked or ineligible incident text** never reaches a model.

### D. Resolution and the caller loop
- **D1 Auto-resolve.** Comment to caller: the fix as numbered steps, the article it came from,
  and "If this did not fix it, reply here and an engineer will take over." State → Resolved by
  BARQ AI Agent. Card: *Resolved by AI — waiting for caller*, with the window end time.
- **D2 Caller accepts** (the portal ticket page's out-of-the-box *Close* action — verified in
  the *Incident Standard Ticket Actions* widget, which also offers *Reopen*). Card: *AI resolution
  confirmed*; incident closed; article credited.
- **D3 Caller silent through the window** (default 3 days, 30 minutes for the demo). Agent
  closes it as confirmed; credited.
- **D4 Caller reopens** (any comment while resolved — out-of-the-box rule). Agent reads the
  comment, records that its fix failed, writes an engineer brief (what was tried, what the
  caller said, closest alternatives), assigns the group's queue, locks itself out. Card:
  *Reopened — now with an engineer*.
- **D5 Reopened again after an engineer fix.** Normal ServiceNow handling; the agent stays
  locked out; counts on the console.
- **D6 Out-of-the-box autoclose** (7 days) closes a resolved incident before our window
  ends — only possible if the window is set above 7 days; the setting forbids that.
- **D7 No caller to confirm.** Never auto-resolved; *Suggested — engineer to apply*.
- **D8 Caller asks a question instead of reopening.** In ServiceNow any caller comment on a
  resolved incident reopens it, so this is handled as D4 — the engineer answers.

### E. Engineers and approvers
- **E1 Approve.** From the card or *Needs me*. Agent resumes from the pause and applies the fix
  per decision table. Decision recorded once, with the approver's ServiceNow name.
- **E2 Edit & approve.** Approver edits the fix text; the edited text is applied and offered as
  a knowledge proposal.
- **E3 Reject.** Nothing applied; reason recorded; human lock on; incident stays with the group.
- **E4 Take over.** Any engineer can lock the AI out of an incident at any time; a running
  agent stops before its next write.
- **E5 Release the lock** (approver). The engineer can ask the AI to try again.
- **E6 Engineer resolves manually after an escalation.** Their close notes can be turned into a
  knowledge proposal with one click.
- **E7 Two approvers decide at once.** The first wins; the second sees "already decided by …".
- **E8 Engineer without the approver role presses Approve.** Button not shown; the API refuses
  as well.
- **E9 Incident changed while waiting for approval** (closed, locked, priority raised). The
  agent re-reads the incident before writing and stops if it is no longer safe.
- **E10 Approval waits too long.** After a configurable time it is highlighted on the console
  and in the group's queue (no automatic decision).
- **E11 Backend unreachable when the engineer clicks.** Clear error on the card; nothing changes
  in ServiceNow (fixes today's behaviour where *Capture to KB* marks complete even on failure).

### F. More than one ticket
- **F1 Several similar incidents** (semantic clustering, exists). First one is the leader; the
  others wait and then reuse the work — but **each still passes its own risk check and its own
  approval or caller loop** (fixes audit F-1). From the third similar incident the agent creates
  a **Problem**, links them all, and sets the leader as parent incident.
- **F2 Priority-1 follower of a solved cluster.** Parks for approval (never auto-completes).
- **F3 Problem resolved by an engineer.** On this instance the out-of-the-box rule
  (*SNC - ITIL - Update Related Incidents*) only adds the problem's close notes as a work note
  to linked incidents and moves On Hold ones back to In Progress — it does **not** resolve them.
  We add the missing step: the agent sends the problem's fix to each linked incident's caller
  and resolves it through the normal caller loop (D1–D4), so each caller can still reopen.
- **F4 Two issues in one ticket.** Agent proposes a split (approval): a new incident for the
  second issue, linked, same caller.
- **F5 Exact duplicate of an open incident.** Agent proposes *Duplicate* (approval), links both.

### G. Failures and recovery
- **G1 Model provider down / timeout.** Retries with backoff; then *Failed — can retry*; DLQ.
- **G2 ServiceNow down during a write.** Retried; the write is idempotent (receipt + read-back),
  never doubled.
- **G3 Qdrant down.** Retrieval fails → retry → escalate as no evidence; never guesses.
- **G4 Worker killed mid-run.** Recovered from the last checkpoint; no duplicate write (NFR-03).
- **G5 Out of memory.** Prevented by concurrency 2 and per-child memory limits; if it still
  happens the reaper re-queues the run.
- **G6 Retry from ServiceNow.** *Retry* on the card / console re-sends the same event; the old
  failure stays in the audit trail.
- **G7 ServiceNow and backend disagree** (today: 92 vs 35 awaiting). A sync report on the
  console lists every mismatch with a one-click fix (re-queue, mark failed, or release).

### H. Chat assistant
- **H1 Knowledge question.** Answer with citations from all permitted articles (including
  process articles).
- **H2 "What happened on INC0010245?"** Live read of the incident plus the agent's timeline and
  decisions (incident memory).
- **H3 "Add a work note …" / "assign to Network".** The assistant proposes the exact change;
  nothing happens until the engineer presses Confirm; the change uses the same tools,
  permissions and audit as the agent.
- **H4 Follow-up questions.** Resolved from the conversation ("and step 3?").
- **H5 Memory.** Chats belong to the engineer's ServiceNow login (survive logout and other
  browsers); a short *What I remember* list the engineer can view and delete.
- **H6 Injection or secrets in chat.** Screened and redacted like incidents.
- **H7 Out-of-scope or unknown.** Says so; no invented answer.
- **H8 Daily budget reached.** Polite refusal; admin sees it on the console.

### I. Administration
- **I1 Change autonomy level / window / kill switch.** Takes effect on the next run; audited.
- **I2 New category or new assignment group.** Admin maps category → group in Settings; until
  mapped, the agent leaves assignment to people.
- **I3 New engineer joins.** Admin grants the role; nothing in code changes.
- **I4 Operator secret rotation.** Changed in one ServiceNow property and the server `.env`
  together; the console health check confirms the bridge works.

### J. Data hygiene
- **J1 Test data.** Test incidents are titled `[BARQ-TEST-…]` and listed on the console
  separately.
- **J2 Cleaning the existing instance.** See section 12.

### L. Edge cases found while checking the catalogue
- **L1 Vague incident** ("it doesn't work"). Instead of escalating as low confidence (today),
  the agent asks the caller one specific question as a comment and sets *On Hold — Awaiting
  Caller* (an existing hold reason). The caller's reply moves it back to In Progress and the
  agent runs again with the answer.
- **L2 Engineer changes assignment, category or priority while the agent is running.** The
  agent re-reads the incident before every write and never overwrites a field a person changed
  after the run started.
- **L3 The agent's own writes must not re-trigger it.** The eligibility rule only watches
  active, category, AI Enabled and Human Lock; new tools that write other fields are checked
  against this so no loop is possible.
- **L4 Caller cancels the incident while the agent is running.** The pre-write re-read sees
  Canceled and stops.
- **L5 Attachments and screenshots.** The agent does not read attachments; the card says so
  when an incident has them, and the decision table treats the evidence as incomplete.
- **L6 Very long or non-English descriptions.** Bounded input (existing limits); the model is
  instructed to answer in the caller's language for caller-facing comments; screening applies
  equally.
- **L7 Several incidents from the same caller at once.** Treated independently unless they
  cluster (F1) or are duplicates (F5).
- **L8 The confirmation window is not polling.** The window check runs inside ServiceNow (a
  scheduled job in our scope) or as a delayed task the backend scheduled when it resolved the
  incident. The backend never polls ServiceNow for work (PRD FR-05).
- **L9 SLA.** AI resolution stops the resolution SLA the normal way; a reopen restarts it. The
  console shows AI time-to-resolve beside the SLA.
- **L10 Time zones.** All backend times are UTC; ServiceNow shows each user's own time zone.

## 9. Knowledge lifecycle — who can add knowledge

1. **Seeded corpus** (exists): the manual and the curated articles, ingested by script.
2. **Engineer writes an article in ServiceNow** → indexed automatically on publish (B7).
3. **Engineer's fix on an escalated incident** → knowledge proposal → **knowledge approver**
   publishes → indexed → used next time (B3, E2, E6).
4. **Feedback**: confirmed AI resolutions credit an article; reopens penalise it (B6).
5. **Only** the knowledge approver role (and the `kb_publisher` service under a recorded
   approval) can publish. The agent never publishes on its own.

## 10. Memory (shared by the agent and the chatbot)

| Layer | What it holds | Who reads it | Stored in | Retention / control |
|---|---|---|---|---|
| Incident memory | every agent step, evidence used, decisions, the caller's feedback, reopen reasons | the agent on reopen or re-run; the chatbot when asked about that incident; the card timeline | PostgreSQL (existing execution + workflow + approval rows, plus a small feedback table) | life of the incident |
| Engineer memory | that engineer's conversations and a few explicit facts ("I'm on Network") | the chatbot for that engineer only | PostgreSQL (chat tables from #213, keyed by ServiceNow user) | viewable and deletable by the engineer; never shared |
| Team memory | knowledge articles, plus the confirmed / failed counts per article | the agent and the chatbot | ServiceNow KB + Qdrant + PostgreSQL counters | knowledge approver curates |

Not built: storing every chat as searchable memory (privacy and cost, little benefit).
All memory text is screened and redacted before it is stored.

## 11. The ServiceNow experience after the change

### 11.1 Incident form — new "BARQ AI" view (default only after approval)
- **Top: AI Assistant card** — status chip, confidence bar, the fix as numbered steps with
  links to the articles, one line explaining why (resolved / paused / reopened), window end
  time, and the buttons the current user is allowed to use (Approve, Edit & approve, Reject,
  Take over, Release lock, Re-run, Retry).
- **Below: the standard incident fields**, unchanged in position, so engineers find what they
  expect.
- **Tab "AI timeline"** — each agent step with time, tool, result, and the caller's responses.
- **Tab "AI diagnostics"** (admins) — model, agent version, timestamps, retry count, failure
  reason, execution ID, trace link: the 16 technical fields that today fill the AI tab.
- Old *Approve AI Suggestion*, *Refuse AI Suggestion*, both *Approve & Capture to KB* and
  *Replay AI Incident* buttons: **deactivated** (not deleted) and replaced by the card.

### 11.2 Lists
- Incident list column **AI status** with colours (red needs approval, amber reopened, green
  confirmed, grey suggested).
- Saved filters: *Needs my approval*, *Resolved by AI — waiting*, *Reopened from AI*, *AI
  failed*.

### 11.3 One navigation menu: **BARQ AI**
Console · Needs me · AI-resolved incidents · Execution log · Knowledge proposals · Settings.
Replaces today's three scattered entries (an application titled "Ai", a "Dead-Letter Queue"
module inside it, and an "AI Dead-Letter Queue" module filed under the standard Incident menu).

### 11.4 BARQ AI Console (one React page inside ServiceNow)
- **Live** — incidents the agent is working on now, with their step.
- **Needs me** — approvals and reopened incidents for my groups, oldest first, with the brief.
- **Results** — AI-resolved, confirmed, reopened, escalated, failed; time to resolve; top
  articles; failing articles.
- **Chat** — the assistant, opened on an incident when launched from the card.
- **Failed & sync** — DLQ with retry; the ServiceNow vs backend mismatch report.
- **Settings** (admin) — autonomy, window, category → group map, kill switch, role holders,
  bridge health.

### 11.5 Caller experience (portal)
The caller sees the agent's comment on their ticket, clearly signed *BARQ AI Agent*, and can
reply or reopen with the standard portal actions. No email (switched off on the instance).

## 12. Cleaning the current instance (proposal — needs Ali's decision per item)

Facts (2026-10-03): 173 incidents created since 2026-09-09 — 172 still *New*, none with an
assignment group or service, 144 with priority *5 - Planning*, callers mostly *System
Administrator* or empty, 49 with test-style titles; ServiceNow shows 92 *awaiting approval*
while the backend has 35 parked runs; 60 of the 84 published articles in the main knowledge
base are process articles and 14 have no category; 24 problems; duplicate UI actions; three
scattered menu entries; a disabled global business rule left behind.

| Item | Proposed action | Reversible? |
|---|---|---|
| Test incidents (team's and ours) | never deleted; move to Canceled with note "BARQ test data — archived 2026-10-03", AI Enabled off, Human Lock on; listed on the console | yes — reopen |
| 35 parked backend runs | close as `cancelled` with reason "test data archived" | **no** (approval rows are immutable by design) — needs explicit OK; alternative: leave them and hide from *Needs me* |
| 92 ServiceNow "awaiting approval" | reconcile with the sync report (G7) | yes |
| Duplicate / broken UI actions | deactivate | yes |
| Menus | one BARQ AI menu; old entries deactivated | yes |
| Disabled global rule "AI Enforce Human Lock Safety Stop" | leave disabled (it is in Global scope; removing needs the admin and the team's OK) | — |
| Knowledge without category | report on the console for the knowledge approver | — |
| Hard-coded operator secret in a UI action script | removed; read from the property | yes |
| Agent display name | `ai_orchestrator_svc` shown as *BARQ AI Agent* | yes |

## 13. Data and configuration changes

- ServiceNow (our scope): one choice field `ai_outcome`; properties for autonomy level,
  confirmation window, kill switch, category → group map; field ACLs for the new tools; a
  business rule for knowledge publish/retire events; a business rule raising a minimal event
  on reopen of an AI-resolved incident; the console UI page, the scripted REST bridge, the
  form view, formatter and UI actions; one menu.
- Backend: migrations for incident feedback / article counters / engineer memory (plus the
  chat tables from #213), each with a working downgrade; settings mirrored from ServiceNow
  properties via the bridge (no polling: ServiceNow pushes on change).
- No change to instance-wide properties (autoclose stays 7 days).

## 14. Reversibility

| Change | How to undo |
|---|---|
| Code | lives on `feat/barq-agentic-servicenow`; `main` untouched until a reviewed merge |
| Backend on the shared EC2 | before deploy: `pg_dump`, Qdrant snapshot, `.env` copy, recorded SHA; rollback script downgrades our migrations, checks out the previous SHA, rebuilds, verifies `/ready` and the worker ping — rehearsed once before testing |
| ServiceNow configuration | every admin change inside one named update set (back-out-able); our app records are scoped and tracked; old UI actions deactivated, not deleted; form view separate until approved |
| Settings | autonomy can be dropped to *Suggest only* or the kill switch turned on instantly |
| Data written during tests | test incidents labelled; nothing deleted |

## 15. Test plan

Unit and integration tests for every tool, rule and decision path (no gate skipped), then live
tests on the shared system, one per scenario: A1–A7, B1–B3, B6–B7, C1–C3, C5–C6, D1–D4, D7,
E1–E4, E7–E9, E11, F1–F2, F4, G2, G4, G6–G7, H1–H5, I1, L1–L4, L8, and after the remediation phases K1–K10. Each live test records the ServiceNow
read-back, the PostgreSQL rows and the Langfuse trace, and is labelled `[BARQ-TEST-…]`.
Caller-side steps (accept / reopen) are performed through the portal as an existing demo user.

## 16. Delivery plan — tasks in priority order

Work is tracked as tasks, not hours. Each task is done only when its tests pass (unit, then
live where listed). Tasks are ordered so that whatever point is reached, everything before it
is complete, tested and deployable. Estimated total ≈ 16 h of continuous work, dominated by
live model latency, builds and deploys rather than writing code.

| ID | Task | Done when | Scenarios proven |
|---|---|---|---|
| T0 | Safety: DB dump, Qdrant snapshot, `.env` copy, SHA, ServiceNow config snapshot, rollback script | backups on EC2 + local; script passes shellcheck — **done 2026-10-03 05:10** | — |
| T1 | Critical fixes: follower governance + no silent follower success (F-1/F-2); no decision without a paused interrupt (F-8); secrets inside JSON redacted (F-4); approval path cannot default to approved | unit tests for each | C6, F1, F2, E7 |
| T2 | Agent tools: assign, set In Progress, comment to caller, resolve, hand over; decision table; autonomy setting (Suggest / Assist / Autonomous / off); pre-write re-read | unit tests; registry permit/refuse tests | A1, A3, A7, D1, D7, L2, L4 |
| T3 | ServiceNow foundation (one update set): field ACLs for new writes, agent display name, bridge script include reading the secret from the property, Approve / Reject / Take over / Retry actions calling the backend with the engineer's name, roles on actions, old actions deactivated | actions work from a ServiceNow session | E1, E3, E4, E8, E11, G6 |
| T4 | Gate + hand deploy to the shared EC2 (worker concurrency 2) + rollback rehearsal | all suites green; `/ready`; rollback proven once | G5 |
| T5 | Live core tests | evidence for each | A1, A3, A4, C1, C5, C6, D1, E1, E3, F2 |
| T6 | Caller loop: confirmation window (in-instance job), accept/silent → confirmed + close, reopen → hand-over brief + lock + article penalty; vague incident → ask caller (On Hold) | unit + live | D2, D3, D4, D8, L1, B6 |
| T7 | Incident form: *BARQ AI* view with AI card, timeline tab, diagnostics tab, list status column + filters, one BARQ AI menu | visible and working on the instance | 11.1–11.3 |
| T8 | BARQ AI Console (React UI page + scripted REST): Live, Needs me, Results, Failed & sync, Settings | page works with real data | G7, I1, I2 |
| T9 | Chat in the console + memory (incident, engineer, team) + confirmed actions; Streamlit removed | unit + live | H1–H8 |
| T10 | Knowledge: publish/retire sync event, full KB ingestion, proposals from human fixes, article credit/penalty | live | B3, B7, B6 |
| T11 | Multi-ticket: cluster → Problem + parent links, problem resolved → linked incidents through caller loop, split, duplicate | unit + live | F1, F3, F4, F5 |
| T12 | Remediation phase 1: runbook catalog, `run_runbook`, catalog orders, standard changes, incident task, account unlock | unit + live | K1–K4, K6–K10 |
| T13 | Remediation phase 2: lab runner with verify/rollback | live | K5 |
| T14 | Instance clean-up (per Ali's decisions) + sync report reconciliation | report clean | J1, J2, G7 |
| T15 | Docs: design status, operations runbook, evidence, PR description | PR ready for review | — |
| T16 | Investigation loop: bounded observe → reason → READ tool → observe, over journal, caller history, similar open incidents, CI, recent changes, attachments, knowledge | unit (step budget, read-only tools, evidence recorded) + live | A, C, F |
| T17 | Risk reassessment both ways (6.3): code-enforced downgrade rules, independent second check, visible note + *AI reassessed* flag + one-click Undo, upgrade → park + Problem proposal; admin switch | unit per rule + live (fake P1 resolved, real outage escalated) | C, F |
| T18 | Natural conversation: clarifying question to the caller (On Hold – Awaiting Caller), read reply and continue, confirm fix; engineer instructions in chat as gated proposals | unit + live | D, H |
| T19 | Vision: read screenshot/log attachments, redacted and screened, quoted as evidence | unit + live | A, B |
| T20 | Learning from outcomes: reopen/confirm credit to article and decision path, outcome history in confidence | unit + live | B, D |
| T21 | Intelligence test set: fake P1s, outages that look minor, missing information, misleading attachments; scored; zero wrong downgrades required | runs in CI on recorded cases and live on the shared system | all |

**Execution order:** T5 → T6 with T18 → T16 with T17 → T7, T8, T9 → T19, T20 → T10–T15.
T21 grows with each step and gates T17's switch.

## 17. Open decisions

1. Default autonomy level (proposed: Autonomous).
2. Confirmation window (proposed: 3 days normal, 30 minutes for testing and demo).
3. Caller-side testing: Ali in the portal as a demo user, or admin impersonation of a demo user.
4. Agent display name *BARQ AI Agent*.
5. Clean-up items in section 12, especially whether to close the 35 parked runs (irreversible)
   or leave them hidden.
6. When three similar incidents should become a Problem (proposed: 3).
7. Who receives the knowledge approver and approver roles.
8. Remediation: which runbooks to include first (proposed: Corp VPN, Install Software,
   account unlock, printer toner standard change), and whether the lab runner (phase 2) is in
   scope.
9. Test requests and changes created on the shared instance are real records; they will be
   labelled and cancelled after testing (never deleted).
10. Reassessment (6.3): decided 2026-10-03 — a P1 judged low risk is handled as low risk
    (option a), with the safeguards in 6.3. Still open: whether the switch is on by default
    before T21 has enough cases.

## 18. Deadline handling

Tasks are executed strictly in the order of section 16. If the deadline arrives part-way, the
completed prefix is deployed, tested and documented; the rest stays planned here. Checkpoints
after T1, T4 and T5: if one fails, later tasks wait — tests are never cut.

### Risks and how they are handled
- **Shared system during a deadline.** Our branch runs on the shared EC2 from T4; the rollback
  script returns it to `main` in minutes. The team is asked not to merge to `main` meanwhile.
- **ServiceNow changes affect everyone.** All in one update set; old actions deactivated, not
  deleted; the new form view is separate from the default.
- **New permissions for the agent user.** Field-level only, in the update set, backed out with it.
- **Approvals tomorrow.** Nothing merges today; the PR is ready for review.
