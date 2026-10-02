BARQ
SYSTEMS

# I T   O P E R A T I O N S      D E P A R T M E N T

# IT Service Desk

# Operations Manual

FY2026 · Internal Document

APPLIES TO: Service desk analysts, resolver groups, service owners and delivery managers
COVERS: Incident management, escalation, the knowledge base, problems and known errors, change, and the AI Suggested Response pilot
SITES: Dubai and Cairo · single queue, follow-the-sun
DOCUMENT OWNER: Nourhan Abdelrahman — Service Delivery Manager, IT Operations
PUBLISHED: 11 August 2026
NEXT REVIEW: 10 August 2027
CLASSIFICATION: Internal. Not for distribution outside BARQ Systems or its contracted service partners.

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Contents

Document control ....... 5
1. About this manual ....... 6
1.1 Purpose and audience ....... 6
1.2 How to find things ....... 6
1.3 Conventions ....... 7
2. The service desk ....... 8
2.1 Operating model ....... 8
2.2 Channels ....... 8
2.3 Roles ....... 9
2.4 Contact routes ....... 9
3. Incident management ....... 10
3.1 The lifecycle ....... 10
3.2 Incident or request ....... 10
3.3 Priority ....... 10
3.4 Response and resolution targets ....... 11
3.5 Holds, chasing and the no-contact rule ....... 11
3.6 Journalling ....... 12
4. Escalation ....... 13
4.1 When to escalate ....... 13
4.2 The escalation matrix ....... 13
4.3 The desk card ....... 14
5. Service catalogue and supported estate ....... 15
5.1 How to read this catalogue ....... 15
5.2 The catalogue ....... 15
5.3 Criticality definitions ....... 16
6. Knowledge base articles ....... 17
6.1 Article index ....... 17
6.2 Symptom finder ....... 17
6.3 KB0005 — the archived scan ....... 18
6.4 KB0001 — VPN authentication fails after a password change ....... 18
6.5 KB0002 — Outlook shows Disconnected and no mail is delivered ....... 19
6.6 KB0003 — Mapped shared drive is missing after sign-in ....... 19
6.7 KB0004 — Print jobs queue but nothing prints ....... 20
6.8 KB0005 — Account is locked after repeated failed sign-ins ....... 20
6.9 KB0006 — Multi-factor authentication after a lost or replaced device ....... 21

Edition 4.0

2 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

6.10 KB0007 — Laptop performance degrades after a system update.........21
6.11 KB0008 — SAP GUI connection times out with RFC_ERROR_COMMUNICATION.........22
6.12 KB0009 — Wi-Fi drops repeatedly on the 5 GHz corporate network.........22
6.13 KB0010 — Order service connection pool exhaustion.........23
7. Worked incident records.........25
7.1 What the record looks like.........25
7.2 INC0010023 — VPN authentication failure after a password reset.........25
7.3 INC0010047 — a fault with no article behind it.........26
7.4 INC0010064 — three symptoms, one cause.........27
7.5 INC0010052 — escalated without being touched.........28
8. Problems and known errors.........29
8.1 The difference, and why it matters at the desk.........29
8.2 Open problem register.........29
8.3 Known error register.........29
9. Major incident report MIR-2026-03.........31
9.1 Summary.........31
9.2 What happened.........31
9.3 Timeline.........31
9.4 The bridge whiteboard.........32
9.5 Root cause.........32
9.6 Actions.........33
10. Change management.........34
10.1 What the desk needs to know.........34
10.2 Pre-approved standard changes.........34
10.3 Emergency approval.........34
10.4 Approval record — CHG0030455 against INC0010052.........35
11. The AI Suggested Response pilot.........36
11.1 What it does, and what it may not do.........36
11.2 How a suggestion is produced.........37
11.3 Eligibility.........38
11.4 Three gates, and what each outcome means to you.........39
11.5 Where the drafts come from.........40
11.6 Safety controls.........41
11.7 Configuration.........41
11.8 Reading the run log.........42
11.9 Integration payload.........42
12. Reporting.........44
12.1 The monthly service review.........44

Edition 4.0

**3** of 52 ·

12.2 Reporting to service delivery managers......44

12.3 What is not reported ......45

Appendix A · Glossary ......46

Appendix B · Templates ......47

B.1 Escalation handover......47

B.2 Requester update ......47

B.3 No-contact resolution......47

B.4 Knowledge article proposal......47

Appendix C · Impact and urgency worksheet......49

Appendix D · Directory ......50

Appendix E · Identifier index ......51

Edition 4.0                                                                                                    **4** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Document control

This manual is a controlled document. The copy of record is the published version in the knowledge base; printed and downloaded copies are uncontrolled and may be superseded without notice. Check the edition and the review date on the cover before relying on any procedure in it.

# Version history

<table><tr><th>EDITION</th><th>DATE</th><th>AUTHOR</th><th>SUMMARY OF CHANGE</th><th>APPROVED BY</th></tr><tr><td rowspan="2">4.0</td><td>11 Aug 2026</td><td>H. Moawad, Knowledge Manager</td><td>Service catalogue refreshed against the FY2026 estate. Section 11 added for the AI Suggested Response pilot.</td><td>N. Abdelrahman</td></tr><tr><td>11 Aug 2026</td><td>D. Halim, Problem Manager</td><td>Known error register aligned to the problem records raised after MIR-2026-03.</td><td>N. Abdelrahman</td></tr><tr><td rowspan="2">3.2</td><td>02 Apr 2026</td><td>H. Moawad, Knowledge Manager</td><td>KB0010 revised to version 2 following the 14 March order-processing outage. Version 1 retired.</td><td>K. Selim</td></tr><tr><td>02 Apr 2026</td><td>O. Sabry, Service Desk Team Lead</td><td>Priority matrix corrected: P2 now requires a named service owner on the bridge.</td><td>K. Selim</td></tr><tr><td>3.1</td><td>19 Jan 2026</td><td>H. Moawad, Knowledge Manager</td><td>Escalation matrix updated for the Identity &amp; Access reorganisation.</td><td>N. Abdelrahman</td></tr><tr><td>3.0</td><td>06 Oct 2025</td><td>H. Moawad, Knowledge Manager</td><td>Annual review. Manual restructured into twelve sections and the identifier index added.</td><td>N. Abdelrahman</td></tr></table>

# Ownership and review

<table><tr><td><b>Document owner</b></td><td>Nourhan Abdelrahman — Service Delivery Manager, IT Operations</td></tr><tr><td><b>Author and maintainer</b></td><td>Hesham Moawad — Knowledge Manager</td></tr><tr><td><b>Classification</b></td><td><b>Internal.</b> Not for distribution outside BARQ Systems or its contracted service partners.</td></tr><tr><td><b>Review cycle</b></td><td>Annual, or within 20 working days of any major incident report that names a procedure in this manual</td></tr><tr><td><b>Current edition</b></td><td>4.0 · published 11 August 2026</td></tr><tr><td><b>Next scheduled review</b></td><td>10 August 2027</td></tr><tr><td><b>Feedback</b></td><td>Raise a request against the <i>Knowledge — content correction</i> catalogue item, or comment on the article in the knowledge base</td></tr><tr><td><b>External contribution</b></td><td>Section 11 was drafted with Sprints (sprints.ai) during the AI Suggested Response pilot</td></tr></table>

Edition 4.0

**5** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 1. About this manual

# 1.1 Purpose and audience

This manual is the working reference for everyone who handles an incident at BARQ Systems: service desk analysts on Tier 1, the resolver groups they escalate to, the service owners who accept those escalations, and the delivery managers who report on the result. It states what we have agreed to do, how quickly, and who decides when the agreed answer does not fit.

It is deliberately operational. It does not describe architecture, it does not justify tooling choices, and it does not replace vendor documentation. Where a procedure depends on a system whose behaviour we do not control, the procedure says so and names the team who owns the relationship.

<table><tr><td>Tier 1 analysts</td><td>Sections 3, 4 and 6 are your working set. Section 7 shows what a well-handled ticket looks like end to end.</td></tr><tr><td>Resolver groups</td><td>Sections 4, 5 and 8. Read the known error register before you accept an escalation — half of what reaches you is already documented.</td></tr><tr><td>Service owners</td><td>Sections 5, 9 and 10. Your acceptance criteria for a change and your obligations during a major incident are here.</td></tr><tr><td>Delivery managers</td><td>Sections 3, 9 and 12. The SLA definitions in 3.4 are the ones reported against in the monthly service review.</td></tr><tr><td>New joiners</td><td>Read Sections 1 to 4 in your first week. Do not read Section 6 end to end — it is a reference, and you will search it, not remember it.</td></tr></table>

# 1.2 How to find things

Everything in this manual carries an identifier that is also searchable in the ticketing system. If you have the identifier, search there first — the record is live and this document is a snapshot.

<table><tr><th>PREFIX</th><th>RECORD TYPE</th><th>WHERE IT LIVES</th></tr><tr><td>KB · KB0001</td><td>Knowledge article</td><td>Section 6 of this manual, and the published knowledge base. The manual carries the text as at the edition date; the knowledge base carries the current version.</td></tr><tr><td>INC · INC0010023</td><td>Incident</td><td>The ticketing system. Section 7 reproduces a small number of closed incidents as worked examples.</td></tr><tr><td>PRB · PRB0040012</td><td>Problem</td><td>The problem register. Open problems are summarised in Section 8.</td></tr><tr><td>KE · KE0000034</td><td>Known error</td><td>The known error register in Section 8.2. Every known error names its workaround and its permanent fix, if one is planned.</td></tr><tr><td>CHG · CHG0030455</td><td>Change</td><td>The change calendar. Section 10 covers the procedure; the calendar is authoritative for dates.</td></tr><tr><td>RITM · RITM0010877</td><td>Request item</td><td>The service catalogue. Requests are not incidents — see 3.2 for the distinction and why it matters.</td></tr><tr><td>MIR · MIR-2026-03</td><td>Major incident report</td><td>Section 9. One report per declared major incident, published within ten working days.</td></tr></table>

Edition 4.0

**6** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

Appendix E lists every identifier used in this manual against the section it appears in. If someone quotes you a number and you do not recognise it, start there.

# 1.3 Conventions

▪ **Field labels** appear in Title Case as they render on the form — Assignment group, Business service. Underlying column names appear in code style, as `assignment_group`, and only where a procedure requires you to type one.

▪ **Times** are Gulf Standard Time (UTC+04) unless a clock is named otherwise. Cairo-based staff should note that the service desk runs on GST, not on local time.

▪ **Working hours** means 08:00 to 18:00 GST, Sunday to Thursday. **Extended hours** and **out of hours** are defined in 2.1 and are not interchangeable.

▪ **Must**, **should** and **may** carry their ordinary contractual weight. A step marked Must has been agreed with a service owner and skipping it is a deviation to be recorded, not a judgement call.

Edition 4.0

**7** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 2. The service desk

# 2.1 Operating model

The desk runs a follow-the-sun pattern across two locations with a single queue. An analyst in either location can pick up any incident; the shift pattern determines who is expected to, not who is permitted to.

<table><tr><th rowspan="2">COVERAGE</th><th colspan="2">DUBAI</th><th colspan="2">CAIRO</th></tr><tr><th>HOURS (GST)</th><th>ANALYSTS</th><th>HOURS (GST)</th><th>ANALYSTS</th></tr><tr><td>Working hours</td><td>08:00 – 18:00</td><td>6</td><td>09:00 – 19:00</td><td>5</td></tr><tr><td>Extended hours</td><td>18:00 – 22:00</td><td>2</td><td>—</td><td>—</td></tr><tr><td>Out of hours</td><td>on-call</td><td>1 + escalation</td><td>on-call</td><td>—</td></tr><tr><td>Friday</td><td>on-call</td><td>1</td><td>10:00 – 16:00</td><td>2</td></tr><tr><td>Saturday</td><td>—</td><td>—</td><td>10:00 – 16:00</td><td>2</td></tr></table>

Cairo covers Saturday; Dubai covers Friday

The two locations do not observe the same weekend, and the rota is built around that rather than in spite of it. An incident raised on a Friday morning is a Cairo incident by default, and one raised on a Saturday is a Dubai on-call incident only if it is P1 or P2.

# 2.2 Channels

<table><tr><th>CHANNEL</th><th>HOURS</th><th>CREATES</th><th>NOTES</th></tr><tr><td>Self-service portal</td><td>24/7</td><td>Incident or request</td><td>Preferred. The requester chooses the category, which is why category is unreliable and is re-checked at triage.</td></tr><tr><td>Telephone</td><td>Working + extended</td><td>Incident</td><td>Analyst raises the record while on the call. Never close a phone incident without a written summary in the journal.</td></tr><tr><td>Email to the desk</td><td>24/7</td><td>Incident</td><td>Parsed into an incident with category <i>inquiry</i>. Always re-categorise at triage; the parser cannot.</td></tr><tr><td>Teams channel</td><td>Working hours</td><td>Nothing</td><td>Triage and chase only. A conversation is not a ticket. If it turns into work, raise the record and post the number back.</td></tr><tr><td>Monitoring alert</td><td>24/7</td><td>Incident</td><td>Raised automatically against the affected service with priority derived from the alert severity. See 3.3.</td></tr></table>

Edition 4.0

**8** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 2.3 Roles

<table><tr><td>Tier 1 analyst</td><td>Owns the incident from creation to resolution or handover. Applies documented knowledge, keeps the requester informed, and escalates on the clock rather than on frustration. An analyst may not close an incident they escalated without confirmation from the resolver group.</td></tr><tr><td>Team lead</td><td>Owns the queue, not the tickets. Runs triage twice a day, rebalances load, and is the first authority on a priority dispute. Approves any P3 that an analyst wants to raise to P2.</td></tr><tr><td>Resolver group</td><td>Accepts escalations within its service scope. May reject an escalation once, with a written reason and a named alternative group. A second rejection goes to the service owner, not back to the desk.</td></tr><tr><td>Service owner</td><td>Accountable for the service in Section 5. Approves emergency changes against it, joins the bridge for P1 and P2, and signs off the post-incident review.</td></tr><tr><td>Major incident manager</td><td>A rostered role, not a job title. Takes command when a major incident is declared, and holds it until the review is published. During that period their instruction outranks this manual.</td></tr><tr><td>Knowledge manager</td><td>Owns Section 6. Publishes, revises and retires articles, and is the only role that may set an article to retired.</td></tr></table>

# 2.4 Contact routes

The full directory, including out-of-hours numbers, is in Appendix D. The routes below are the ones needed during an incident and are repeated there.

<table><tr><th>NEED</th><th>ROUTE</th><th>WHEN</th></tr><tr><td>Raise or chase an incident</td><td>Portal, then phone</td><td>Any time. Chasing by Teams does not update the clock.</td></tr><tr><td>Declare a major incident</td><td>Major incident bridge</td><td>P1 always. P2 when two or more services are affected.</td></tr><tr><td>Reach a resolver group out of hours</td><td>On-call rota, Appendix D</td><td>P1 and P2 only. P3 waits for working hours.</td></tr><tr><td>Emergency change approval</td><td>Change manager, then service owner</td><td>Both are required. Neither alone is sufficient — see 10.3.</td></tr><tr><td>Knowledge correction</td><td>Catalogue item <i>Knowledge — content correction</i></td><td>Any time. Do not edit a published article directly.</td></tr></table>

Edition 4.0

9 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 3. Incident management

# 3.1 The lifecycle

An incident is an unplanned interruption to a service, or a reduction in its quality. It moves through six states. Only two of them stop the SLA clock, and knowing which two is most of what an analyst needs to understand about the process.

<table><tr><th>STATE</th><th>VALUE</th><th>MEANS</th><th>CLOCK</th><th>WHO MAY SET IT</th></tr><tr><td>New</td><td>1</td><td>Created, not yet picked up.</td><td>Running</td><td>Anyone. Set automatically on creation.</td></tr><tr><td>In Progress</td><td>2</td><td>An analyst or resolver group is working it.</td><td>Running</td><td>The assignee.</td></tr><tr><td>On Hold</td><td>3</td><td>Waiting on the requester, a supplier or a scheduled window.</td><td>Paused</td><td>The assignee, with a reason code. See 3.5.</td></tr><tr><td>Resolved</td><td>6</td><td>A fix has been applied and the requester has been told.</td><td>Stopped</td><td>The assignee.</td></tr><tr><td>Closed</td><td>7</td><td>Resolved and either confirmed or auto-closed after five working days.</td><td>Stopped</td><td>Automatic, or the requester.</td></tr><tr><td>Cancelled</td><td>8</td><td>Raised in error, duplicate, or not an incident.</td><td>Stopped</td><td>Team lead only.</td></tr></table>

# 3.2 Incident or request

Something broken is an incident. Something wanted is a request. The distinction decides the SLA, the approval path and the reporting line, so it is settled at triage and not later.

<table><tr><th>INCIDENT</th><th>REQUEST</th></tr><tr><td>▪ "I cannot connect to the VPN since my password reset." ▪ "Outlook says Disconnected and no mail has arrived since 08:00." ▪ "The shared drive that was mapped yesterday is gone." ▪ "The order service is returning 500s."</td><td>▪ "Please order me a second monitor." ▪ "I need access to the finance folder." ▪ "When will my expense claim be paid?" ▪ "Can you install the design suite on my laptop?"</td></tr><tr><td>Resolution targets in 3.4. No approval required to work it.</td><td>Fulfilment targets are set per catalogue item. Most require an approval before any work begins.</td></tr></table>

Two cases cause most of the argument. **Access that used to work and has stopped** is an incident — something broke. **Access that never existed** is a request, even when the person urgently needs it. And **a question with no fault behind it** is neither: answer it, log it as an inquiry, and do not let it consume an incident slot.

# 3.3 Priority

Priority is derived, not chosen. Impact and urgency are set at triage and the matrix does the rest. An analyst who wants a different priority changes the impact or the urgency and says why in the journal; they do not overwrite the derived value.

Edition 4.0
10 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><td rowspan="2"></td><th colspan="3">URGENCY</th><td rowspan="2"></td></tr><tr><th>1 – HIGH</th><th>2 – MEDIUM</th><th>3 – LOW</th></tr><tr><td>Impact 1 – Enterprise</td><td>P1</td><td>P1</td><td>P2</td><td>Whole service, or a site</td></tr><tr><td>Impact 2 – Department</td><td>P1</td><td>P2</td><td>P3</td><td>A team or a floor</td></tr><tr><td>Impact 3 – Individual</td><td>P2</td><td>P3</td><td>P4</td><td>One person</td></tr><tr><td colspan="5">A monitoring alert sets impact from the affected service's criticality in Section 5, and urgency from the alert severity. An analyst may lower a derived P1 only with the team lead's agreement, recorded in the journal.</td></tr></table>

# 3.4 Response and resolution targets

The clock starts when the incident is created, not when it is picked up. It pauses on hold and stops on resolved. Targets are measured against working hours for P3 and P4, and against elapsed time for P1 and P2.

<table><tr><th>PRIORITY</th><th>RESPONSE</th><th>RESOLUTION</th><th>UPDATE CADENCE</th><th>CLOCK BASIS</th></tr><tr><td>P1</td><td>15 minutes</td><td>4 hours</td><td>Every 30 minutes</td><td>Elapsed, 24/7</td></tr><tr><td>P2</td><td>30 minutes</td><td>8 hours</td><td>Every 2 hours</td><td>Elapsed, 24/7</td></tr><tr><td>P3</td><td>4 working hours</td><td>3 working days</td><td>Daily</td><td>Working hours</td></tr><tr><td>P4</td><td>1 working day</td><td>5 working days</td><td>On change of state</td><td>Working hours</td></tr></table>

▪ **Response** means a human has read the incident and written something in the journal that is specific to it. An auto-acknowledgement is not a response and does not stop the response clock.

▪ **Resolution** means the service is working again for the requester, confirmed by the requester where they are reachable. A workaround counts as a resolution if the requester can work; the underlying fault then becomes a problem record under Section 8.

▪ **Breach** is recorded automatically and cannot be edited. If a target was missed for a reason outside our control, that reason belongs in the journal and in the monthly service review, not in an adjustment to the record.

# 3.5 Holds, chasing and the no-contact rule

<table><tr><th>REASON CODE</th><th>MAX HOLD</th><th>WHAT MUST HAPPEN BEFORE AND AFTER</th></tr><tr><td>awaiting_user</td><td>5 working days</td><td>Two chases at least one working day apart, both recorded. After the second chase with no reply, resolve with the no-contact resolution code and tell the requester how to reopen.</td></tr><tr><td>awaiting_supplier</td><td>Per contract</td><td>The supplier reference goes in the journal. A hold with no supplier reference is not a supplier hold.</td></tr><tr><td>awaiting_change</td><td>To the window</td><td>The CHG number goes in the journal. The hold is released when the change closes, not when it is approved.</td></tr><tr><td>awaiting_parts</td><td>10 working days</td><td>Expected date recorded and updated weekly. Beyond ten days, raise a request instead and resolve the incident.</td></tr><tr><td>scheduled_with_user</td><td>To the appointment</td><td>Date and time agreed with the requester in writing. Missing an agreed appointment is a breach even when the clock was paused.</td></tr></table>

Edition 4.0

**11** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 3.6 Journalling

The journal is the record. Six months from now, an auditor, a service owner or a colleague picking the ticket up will have nothing else. Two fields, and they are not interchangeable.

<table><tr><th>WORK NOTES · INTERNAL</th><th>ADDITIONAL COMMENTS · THE REQUESTER SEES THESE</th></tr><tr><td>What you checked and what you found</td><td>What you are doing, in the requester's language</td></tr><tr><td>Commands run and their output</td><td>What you need from them, and by when</td></tr><tr><td>Why you ruled a cause out</td><td>What changed and whether they need to act</td></tr><tr><td>Which article you applied, by number</td><td>Never: internal names, host names, colleagues' opinions, or the phrase "as per the KB"</td></tr><tr><td>Who you spoke to and what they said</td><td></td></tr></table>

# Write the article number, every time

"Applied KB0001, cleared the cached credential, confirmed connection" takes four seconds longer to type than "fixed" and is the difference between a knowledge base we can measure and one we cannot. Article usage is reported monthly in Section 12, and it is the only evidence we have for which articles deserve to survive the next review.

Edition 4.0

**12** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 4. Escalation

# 4.1 When to escalate

Escalate on the clock, not on frustration. An incident is escalated when the analyst has exhausted the documented knowledge for its category and the next response target is inside the next hour — whichever comes first. Escalating earlier is not a failure; holding a ticket past its target because you nearly have it is.

<table><tr><th>PRIORITY</th><th>ESCALATE IF UNRESOLVED AFTER</th><th>TO</th><th>AND ALSO</th></tr><tr><td>P1</td><td>15 minutes, or immediately if the cause is unknown</td><td>Resolver group <b>and</b> the major incident bridge</td><td>Service owner paged. Bridge stays open until the service is restored.</td></tr><tr><td>P2</td><td>1 hour</td><td>Resolver group</td><td>Team lead informed. Service owner informed if two or more services are involved.</td></tr><tr><td>P3</td><td>1 working day</td><td>Resolver group</td><td>Nothing further unless the group rejects the escalation.</td></tr><tr><td>P4</td><td>3 working days</td><td>Resolver group, or convert to a request</td><td>Check first that it is not a request in disguise — see 3.2.</td></tr></table>

# 4.2 The escalation matrix

Grouped by the service area that owns the escalation. The first line is the standing route; the second is who takes it if the first does not acknowledge inside the acknowledgement window.

<table><tr><th>AREA</th><th>TRIGGER</th><th>FIRST ROUTE</th><th>IF NO ACK IN 15 MIN</th></tr><tr><td rowspan="3">Network</td><td>VPN, remote access, site links</td><td>Network Operations</td><td>NetOps duty lead</td></tr><tr><td>Corporate Wi-Fi, roaming, coverage</td><td>Network Operations</td><td>NetOps duty lead</td></tr><tr><td>Firewall or segmentation change</td><td>Network Security</td><td>Head of Infrastructure</td></tr><tr><td rowspan="3">Identity</td><td>Lockouts, password policy, directory</td><td>Identity &amp; Access</td><td>IAM duty lead</td></tr><tr><td>MFA reset or device re-enrolment</td><td>Identity &amp; Access</td><td>IAM duty lead</td></tr><tr><td>Suspected compromise</td><td>Security Operations, immediately</td><td>CISO on-call — do not wait 15 minutes</td></tr><tr><td rowspan="3">Applications</td><td>SAP availability or connectivity</td><td>SAP Basis</td><td>SAP service owner</td></tr><tr><td>Order processing and fulfilment</td><td>Platform Engineering</td><td>Head of Platform Engineering</td></tr><tr><td>Mail flow and collaboration</td><td>Collaboration Services</td><td>Collaboration duty lead</td></tr><tr><td rowspan="2">Endpoint</td><td>Laptop, desktop, peripherals</td><td>Endpoint Engineering</td><td>Endpoint duty lead</td></tr><tr><td>Print and reprographics</td><td>Print Services</td><td>Facilities duty manager</td></tr></table>

Edition 4.0

**13** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# A rejected escalation comes back once, and once only

A resolver group may return an escalation with a written reason and a named alternative group. The analyst routes it to that group. If the second group also rejects it, the incident goes to the service owner listed in Section 5 — not back to the desk, and not around the loop again. Two rejections is a routing problem, and routing problems are owned above the desk.

# 4.3 The desk card

The card below is laminated at every desk position and pinned in the Cairo operations room. It is reproduced here because it is the version people actually follow, and because it is reissued whenever 4.1 or 4.2 changes.

Two columns, one figure, one footnote

The webhook returns 202 Accepted before retrieval or generation has run. This is not an optimisation; it is the contract.

ServiceNow's outbound call is synchronous and holds a worker thread while it waits. Retrieval plus generation takes seconds to tens of seconds. A webhook that waited would exhaust the platform's outbound capacity under any realistic burst, slow the instance for every user, and time out anyway.

202 means the service has taken responsibility for the event. That promise is only real because the idempotency key was persisted before the reply was sent.

Answering 202 and then failing to store the key means the event can be replayed into a second suggestion, which is the exact failure the status code was meant to rule out.¹

Order of operations

authenticate ◄ validate ◄ claim key ◄ claim incident ◄ dispatch ◄ return 202

Only the fourth step writes to ServiceNow on the request path.

¹ See KB-12 for the atomic claim statement.

The escalation and acceptance card, issue 4. Reissued with every edition of this manual.

Edition 4.0

**14** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 5. Service catalogue and supported estate

# 5.1 How to read this catalogue

Each service carries a criticality, a service owner, a support window and the resolver group that owns incidents against it. Criticality feeds the impact column of the priority matrix in 3.3, so changing it is a governance decision and not a desk decision.

Support windows are stated in Gulf Standard Time and follow the desk pattern in 2.1¹. A service marked **24/7** is covered by the on-call rota out of hours for P1 and P2 only; P3 and P4 against a 24/7 service still wait for working hours².

# 5.2 The catalogue

<table><tr><th>SERVICE</th><th>CRITICALITY</th><th>OWNERSHIP AND WINDOW</th><th>NOTES</th></tr><tr><td>order-processing</td><td>Tier 1</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>K. Selim</td></tr><tr><td>Group</td><td>Platform Eng</td></tr><tr><td>Window</td><td>24/7</td></tr></table></td><td>Revenue-bearing. Any P1 goes straight to the bridge. Remediation is change-controlled — see KB0010 and CHG0030455.</td></tr><tr><td>identity</td><td>Tier 1</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>N. Abdelrahman</td></tr><tr><td>Group</td><td>Identity &amp; Access</td></tr><tr><td>Window</td><td>24/7</td></tr></table></td><td>Underpins every other service. A lockout here presents as a fault in three or four services at once — see 7.4.</td></tr><tr><td>sap-erp</td><td>Tier 1</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>K. Selim</td></tr><tr><td>Group</td><td>SAP Basis</td></tr><tr><td>Window</td><td>06:00–22:00</td></tr></table></td><td>Not reachable from the internet. Confirm VPN before troubleshooting anything else. KE0000034 applies.</td></tr><tr><td>corporate-email</td><td>Tier 2</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>O. Sabry</td></tr><tr><td>Group</td><td>Collaboration</td></tr><tr><td>Window</td><td>24/7</td></tr></table></td><td>Distinguish a single-user fault from a service event before applying any per-user fix. KB0002.</td></tr><tr><td>corporate-vpn</td><td>Tier 2</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>L. Haddad</td></tr><tr><td>Group</td><td>Network Ops</td></tr><tr><td>Window</td><td>24/7</td></tr></table></td><td>Highest article usage on the desk. KB0001 alone accounts for roughly one in twenty incidents.</td></tr></table>

¹Gulf Standard Time, UTC+04. Cairo staff should note that the desk runs on GST and not on local time — see 1.3.

²A P3 raised against a 24/7 service at 02:00 is picked up when the desk opens. Out-of-hours cover exists for restoration, not for convenience.

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><td>file-services</td><td>Tier 2</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>L. Haddad</td></tr><tr><td>Group</td><td>Infrastructure</td></tr><tr><td>Window</td><td>Working</td></tr></table></td><td>Permission changes never happen from an incident. Raise the access request — 3.2 and KB0003.</td></tr><tr><td>corporate-wifi</td><td>Tier 3</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>L. Haddad</td></tr><tr><td>Group</td><td>Network Ops</td></tr><tr><td>Window</td><td>Working</td></tr></table></td><td>Multiple reports in one area are an infrastructure fault, not several endpoint faults. PRB0040021.</td></tr><tr><td>endpoint</td><td>Tier 3</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>D. Halim</td></tr><tr><td>Group</td><td>Endpoint Eng</td></tr><tr><td>Window</td><td>Working</td></tr></table></td><td>Post-update degradation is expected for 24 hours. Do not act inside that window — KB0007.</td></tr><tr><td>print-services</td><td>Tier 4</td><td><table><tr><th>FIELD</th><th>VALUE</th></tr><tr><td>Owner</td><td>O. Sabry</td></tr><tr><td>Group</td><td>Print Services</td></tr><tr><td>Window</td><td>Working</td></tr></table></td><td>Mechanical faults are a facilities matter and are outside the knowledge base. KB0004 covers queues only.</td></tr></table>

# 5.3 Criticality definitions

<table><tr><th>TIER</th><th>MEANING</th><th>CONSEQUENCES</th></tr><tr><td>Tier 1</td><td>Revenue-bearing, or underpins every other service.</td><td>Impact 1 by default. Emergency change route available. Service owner joins every P1 and P2 bridge.</td></tr><tr><td>Tier 2</td><td>Enterprise-wide productivity.</td><td>Impact 1 when the whole service is down, Impact 2 when a department is affected.</td></tr><tr><td>Tier 3</td><td>Departmental or site-level productivity.</td><td>Impact 2 at most, unless a site is entirely without the service.</td></tr><tr><td>Tier 4</td><td>Convenience. A documented manual workaround exists.</td><td>Impact 3 unless several teams are blocked simultaneously.</td></tr></table>

Edition 4.0

**16** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 6. Knowledge base articles

Nine published articles and one retired revision, reproduced as at edition 4.0. The knowledge base is authoritative; this section is a snapshot for offline and audit use.

# 6.1 Article index

The table below crosses two pages. It is the fastest route from a reported symptom to an article number, and it is the one part of this section worth skimming end to end.

<table><tr><th>ARTICLE</th><th>REPORTED AS</th><th>SERVICE</th><th>CATEGORY</th><th>OWNER</th></tr><tr><td>KB0001</td><td>"VPN says authentication failed since my password reset"</td><td>corporate-vpn</td><td>network</td><td>Network Ops</td></tr><tr><td>KB0002</td><td>"Outlook is Disconnected and no mail is arriving"</td><td>corporate-email</td><td>software</td><td>Collaboration</td></tr><tr><td>KB0003</td><td>"My mapped drive has disappeared since I logged in"</td><td>file-services</td><td>network</td><td>Infrastructure</td></tr><tr><td>KB0004</td><td>"Jobs queue up and nothing comjes out of the printer"</td><td>print-services</td><td>hardware</td><td>Print Services</td></tr><tr><td>KB0005</td><td>"I am locked out and nothing lets me sign in"</td><td>identity</td><td>inquiry</td><td>Identity &amp; Access</td></tr><tr><td>KB0006</td><td>"I changed my phone and MFA no longer works"</td><td>identity</td><td>inquiry</td><td>Identity &amp; Access</td></tr><tr><td>KB0007</td><td>"My laptop has been slow since the update"</td><td>endpoint</td><td>hardware</td><td>Endpoint Eng</td></tr><tr><td>KB0008</td><td>"SAP times out with RFC_ERROR_COMMUNICATION"</td><td>sap-erp</td><td>software</td><td>SAP Basis</td></tr><tr><td>KB0009</td><td>"Wi-Fi keeps dropping on the 5 GHz network"</td><td>corporate-wifi</td><td>network</td><td>Network Ops</td></tr><tr><td>KB0010 v2</td><td>"Order service is returning 500s under load"</td><td>order-processing</td><td>software</td><td>Platform Eng</td></tr><tr><td>KB0010 v1</td><td>Retired 02 Apr 2026 — do not apply. See 6.12.</td><td>order-processing</td><td>software</td><td>Platform Eng</td></tr></table>

# 6.2 Symptom finder

Where the reported words do not match an article title, work from the pattern instead.

Started after a change

A password reset, a phone replacement, a Windows update or a new starter's first login. Check KB0001, KB0005, KB0006 and KB0007 before anything else.

Several services at once

Almost always identity, not the services themselves. Go to KB0005 first and confirm the account state before troubleshooting any individual application.

Only in one place

A location-bound fault is infrastructure. KB0009 for wireless; otherwise raise to Network Operations with the location and the times.

Edition 4.0                                                                                                                                                    **17** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 6.3 KB0005 — the archived scan

Articles predating the 2025 migration exist as scans of the printed originals, signed off by the reviewer of the day. The scan is retained for audit; the published text in 6.8 is what you follow.

RUNBOOK — ACCOUNT LOCKOUT

KB0005 · version 4 · identity

SYMPTOM
The user cannot sign in to any corporate system. Errors mention a
locked or disabled account. Often follows a password change, and
often presents as several services failing at once.

CAUSE
The lockout policy triggers after a threshold of failed attempts. A
cached credential on any device can retry an old password silently
and lock the account repeatedly.

RESOLUTION
1. Verify the user's identity. This step is mandatory.
2. Unlock the account in the identity console.
3. Sign out of the corporate mail profile on the mobile device.
4. Clear cached credentials on the laptop, including VPN.
5. Confirm access to two different services before closing.

ESCALATION
Repeated lockouts with no identifiable source go to the Identity
team. Never disable the lockout policy for an individual user.

REVIEWED BY: H. Moawad
DATE: 11 / 04 / 2026

KB0005 as it existed before the 2025 migration. Retained for audit only — the current text is in 6.8.

# 6.4 KB0001 — VPN authentication fails after a password change

KB0001: VPN AUTHENTICATION FAILS AFTER A PASSWORD CHANGE
State: Published
Version: 2
Service: corporate-vpn
Category: network
Owner: Network Operations
Author: L. Haddad
Reviewed: 11 Apr 2026
Uses, 12 mo: 1,284
Related: PRB0040012, INC0010023

**Symptom.** The user can reach the internet but the VPN client reports an authentication failure. It began after a password reset. The client may report "invalid credentials" even when the new password is entered correctly.

**Cause.** The VPN client caches the previous credential in the operating system credential store. The cached entry is presented before the newly typed password, so the directory rejects it. Repeated attempts can lock the account (see KB0005).

# Resolution.

1. Confirm with the user that they changed their password within the last 24 hours.

2. Ask the user to sign out of the VPN client completely, including the system tray icon.

3. Clear the cached credential for the VPN profile from the credential store.

Edition 4.0

**18** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

4. Reconnect using the new password.

5. If authentication still fails, check whether the account is locked in the identity console before escalating.

**Escalation.** If the account is not locked and the new password works elsewhere, escalate to the Network team with the client log.

# 6.5 KB0002 — Outlook shows Disconnected and no mail is delivered

KB0002: OUTLOOK SHOWS DISCONNECTED AND NO MAIL IS DELIVERED
State: Published
Version: 3
Service: corporate-email
Category: software
Owner: Collaboration Services
Author: O. Sabry
Reviewed: 02 Feb 2026
Uses, 12 mo: 947 · Related INC0010024

**Symptom.** The mail client displays Disconnected or Trying to connect. No new mail arrives. Webmail may still work, or may not.

**Cause.** Two distinct causes present identically: a single-user profile or cached-mode corruption, and a service-wide mail outage. Distinguishing them is the first step, not an afterthought.

# **Resolution.**

1. Ask whether colleagues are affected. If more than one user in the same area is affected, treat it as a service event and stop; do not apply per-user fixes to a platform outage.
2. Check whether webmail works for this user. If webmail works, the fault is client-side.
3. For a client-side fault, close the client fully and reopen it.
4. If it still fails, recreate the mail profile and allow the cache to rebuild.
5. Confirm mail flow before closing.

**Escalation.** If multiple users are affected, raise a major incident against the corporate-email service. Do not resolve individual incidents until the service event is closed.

# 6.6 KB0003 — Mapped shared drive is missing after sign-in

KB0003: MAPPED SHARED DRIVE IS MISSING AFTER SIGN-IN
State: Published
Version: 2
Service: file-services
Category: network
Owner: Infrastructure
Author: L. Haddad
Reviewed: 19 Jan 2026
Uses, 12 mo: 612 · Related INC0010025

**Symptom.** A previously available network drive letter is absent after signing in. Other drives may still be present. Browsing to the server path directly may work.

**Cause.** The mapping script runs before the network is ready, or the user has been removed from the group that grants access to the share. These require different fixes, so establish which one applies before acting.

# Resolution.

1. Ask the user to browse to the server path directly. If it opens, the share and the permissions are fine and the fault is in the mapping.

Edition 4.0

**19** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

2. If the path opens, re-run the mapping script, or remap the drive with reconnect-at-sign-in enabled.
3. If the path is refused, check the user's group membership against the share's access group.
4. If group membership is missing, raise an access request. Do not modify group membership from this incident.
5. Confirm the drive is present after a fresh sign-in before closing.

**Escalation.** Permission changes go through the access request process and are never applied directly from an incident.

# 6.7 KB0004 — Print jobs queue but nothing prints

KB0004: PRINT JOBS QUEUE BUT NOTHING PRINTS
State: Published
Version: 1
Service: print-services
Category: hardware
Owner: Print Services
Author: O. Sabry
Reviewed: 06 Oct 2025
Uses, 12 mo: 1,530 · Related INC0010026, INC0010047

**Symptom.** Documents accumulate in the print queue. The printer shows ready and reports no error. Cancelling a job leaves it stuck as Deleting.

**Cause.** The local print spooler service has stalled, leaving orphaned job files that block the queue.

# Resolution.

1. Confirm the printer is online and shows no physical error, and that paper and toner are present.

1. Confirm the printer is online and shows no physical error.
2. Stop the print spooler service on the affected machine.
3. Delete the queued job files from the spooler directory.
4. Start the print spooler service again.
5. Print a test page and confirm it completes.

**Escalation.** If the queue stalls again within an hour, or several users on the same printer are affected, escalate to Print Services — the fault is likely on the print server rather than the client.

# 6.8 KB0005 — Account is locked after repeated failed sign-ins

<table><tr><th>KB0005</th><th colspan="3">ACCOUNT IS LOCKED AFTER REPEATED FAILED SIGN-INS</th></tr><tr><td>State</td><td>Published</td><td>Version</td><td>4</td></tr><tr><td>Service</td><td>identity</td><td>Category</td><td>inquiry</td></tr><tr><td>Owner</td><td>Identity &amp; Access</td><td>Author</td><td>H. Moawad</td></tr><tr><td>Reviewed</td><td>11 Apr 2026</td><td>Uses, 12 mo</td><td>2,109 · Related PRB0040012, INC0010027</td></tr></table>

**Symptom.** The user cannot sign in to any corporate system. Errors mention a locked or disabled account. Often follows a password change, and often presents as several services failing at once.

**Cause.** The lockout policy triggers after a threshold of failed attempts. A cached credential on any device — a phone mail profile, a VPN client, a mapped drive — can retry an old password silently and lock the account repeatedly, including immediately after each unlock.

# Resolution.

1. Verify the user's identity following the identity verification procedure. This step is mandatory and is never skipped.

2. Unlock the account in the identity console.

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

3. Ask the user to sign out of the corporate mail profile on their mobile device, which is the most common source of a silent retry.

4. Clear cached credentials on the laptop, including VPN and mapped drives.

5. Ask the user to sign in again and confirm access to two different services.

6. If the account locks again within minutes, a device is still retrying an old credential; identify it from the lockout source before unlocking a third time.

**Escalation.** Repeated lockouts with no identifiable source go to the Identity team. Never disable the lockout policy for an individual user.

# 6.9 KB0006 — Multi-factor authentication after a lost or replaced device

KB0006: MULTI-FACTOR AUTHENTICATION AFTER A LOST OR REPLACED DEVICE
State: Published
Version: 3
Service: identity
Category: inquiry
Owner: Identity & Access
Author: H. Moawad
Reviewed: 11 Apr 2026
Uses, 12 mo: 438 · Related RITM0010877, INC0010028

**Symptom.** The user has a new phone, or has lost the previous one, and can no longer approve sign-in prompts or generate codes.

**Cause.** The authenticator registration is bound to the previous device and does not transfer with a phone migration.

# Resolution.

1. Verify the user's identity following the enhanced verification procedure for MFA resets. This is stricter than standard verification and must not be shortened.
2. Confirm whether the previous device is lost or simply replaced. A lost device requires the registration to be revoked, not just re-enrolled.
3. Raise the MFA reset request through the identity workflow. It requires approval; a service desk agent cannot complete it alone.
4. Once approved, guide the user through enrolling the new device.
5. Confirm a successful sign-in with the new factor before closing.

**Escalation.** An MFA reset is a high-risk identity action. It always requires the approval step, and any suspicion of compromise goes to Security immediately.

# 6.10 KB0007 — Laptop performance degrades after a system update

KB0007: LAPTOP PERFORMANCE DEGRADES AFTER A SYSTEM UPDATE
State: Published
Version: 2
Service: endpoint
Category: hardware
Owner: Endpoint Engineering
Author: D. Halim
Reviewed: 19 Jan 2026
Uses, 12 mo: 776
Related: INC0010029

**Symptom.** The machine is noticeably slower after an update. Fans run constantly, applications are slow to launch, and the problem persists across restarts.

Edition 4.0

**21** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

**Cause.** Post-update indexing and driver reinstallation run at high priority for a period after installation. If the degradation persists beyond that period, a driver mismatch is the usual cause.

# Resolution.

1. Ask when the update was installed. Within 24 hours, background indexing is expected — tell the user and check back rather than making changes.
2. Check resource usage and identify the dominant process.
3. If indexing dominates, allow it to complete and confirm with the user the following day.
4. If a graphics or storage driver dominates, reinstall the vendor driver for the installed operating system build.
5. Restart and confirm the machine returns to normal responsiveness.

**Escalation.** If performance is still degraded 48 hours after the update with no dominant process, escalate to Endpoint Engineering with a performance capture.

# 6.11 KB0008 — SAP GUI connection times out with RFC_ERROR_COMMUNICATION

KB0008: SAP GUI CONNECTION TIMES OUT WITH RFC_ERROR_COMMUNICATION
State: Published
Version: 1
Service: sap-erp
Category: software
Owner: SAP Basis
Author: K. Selim
Reviewed: 06 Oct 2025
Uses, 12 mo: 203 · Related KE0000034, INC0010031

**Symptom.** The SAP GUI client fails to connect and reports RFC_ERROR_COMMUNICATION or a connection timeout. Other applications work normally.

**Cause.** The client cannot reach the message server on the required port. Most often the user is off the corporate network without the VPN, or the saved connection entry points at a decommissioned application server.

# Resolution.

1. Confirm the user is on the corporate network or connected to the VPN. SAP is not reachable from the internet.
2. Check the saved connection entry against the current published connection details.
3. If the entry names a specific application server, change it to the message server and group so that load balancing applies.
4. Reconnect and confirm sign-in reaches the logon screen.
5. If the timeout persists from a known-good network, check whether the message server is reachable on its port before escalating.

**Escalation.** A confirmed reachability failure from the corporate network goes to the SAP Basis team with the exact error text and the connection entry used.

# 6.12 KB0009 — Wi-Fi drops repeatedly on the 5 GHz corporate network

KB0009: WI-FI DROPS REPEATEDLY ON THE 5 GHZ CORPORATE NETWORK
State: Published
Version: 2
Service: corporate-wifi
Category: network
Owner: Network Operations
Author: L. Haddad
Reviewed: 02 Feb 2026
Uses, 12 mo: 1,041
Related: PRB0040021, INC0010033

Edition 4.0

22 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

**Symptom.** The connection drops every few minutes and reconnects on its own. It is worse in some parts of the building and while moving between areas.

**Cause.** Aggressive roaming behaviour between access points, or a power-saving setting on the wireless adapter that suspends the radio during idle periods.

# Resolution.

1. Establish whether drops occur in one location or while moving. Drops only while moving indicate roaming; drops while stationary indicate the adapter.
2. For a stationary user, disable power saving on the wireless adapter.
3. Update the wireless adapter driver to the current supported version.
4. Ask the user to forget and rejoin the corporate network so the profile is rebuilt.
5. Confirm a stable connection for at least fifteen minutes before closing.

**Escalation.** Drops affecting several users in the same area are an infrastructure fault. Escalate to Network with the location and the approximate times.

# 6.13 KB0010 — Order service connection pool exhaustion

This article exists in two revisions and both are reproduced, because the retired one is still quoted from memory and its procedure is now harmful.

# Version 1 — retired 02 April 2026

KB0010: ORDER SERVICE CONNECTION POOL EXHAUSTION · **RETIRED**
State: Retired
Version: 1
Retired on: 02 Apr 2026
Reason: Step 1 caused a 40-minute outage on 14 Mar 2026. See MIR-2026-03.

**Symptom.** The order service returns HTTP 500 and logs report that the database connection pool is exhausted.

**Cause.** Connections are not being returned to the pool under load.

# Resolution.

1. Restart the order service application server to clear the pool.
2. Confirm the service returns 200 and monitor for recurrence.
**Escalation.** If it recurs within the hour, escalate to Platform Engineering.

# Why this revision is dangerous, not merely outdated

The restart in step 1 dropped in-flight orders and caused the outage recorded in MIR-2026-03. The procedure is well written and reads convincingly, which is exactly why it kept being applied after the fault it addresses had changed. If you find this text anywhere — a saved copy, a wiki page, a team chat pin — replace it with the link to version 2 and tell the knowledge manager where you found it.

# Version 2 — published 02 April 2026

KB0010: ORDER SERVICE CONNECTION POOL EXHAUSTION
State: Published
Version: 2
Service: order-processing
Owner: Platform Engineering · K. Selim
Reviewed: 02 Apr 2026
Related: MIR-2026-03, PRB0040018, CHG0030455, INC0010052

Edition 4.0

**23** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

**Symptom.** The order service returns HTTP 500 under load. Application logs report that the database connection pool is exhausted and that connection acquisition timed out.

**Cause.** Connections are held beyond their intended lifetime by a long-running query path and are not returned to the pool, so new requests wait and then fail.

# Resolution.

1. **Do not restart the application server.** A restart drops in-flight orders and caused a 40-minute outage on 14 March 2026.
2. Confirm pool saturation from the service metrics dashboard rather than from the error message alone.
3. Notify the Order Processing service owner. This service has a change-controlled remediation path.
4. Raise an emergency change against CHG0030455 for the pool drain procedure, which recycles connections without dropping in-flight work.
5. Apply the drain procedure only once the change is approved.
6. Monitor pool utilisation for thirty minutes after the drain.

**Escalation.** Any incident on `order-processing` at Priority 1 goes to the service owner immediately and is never remediated from the service desk alone.

Edition 4.0

**24** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 7. Worked incident records

Four closed incidents, reproduced with their journals intact. They are here because they are ordinary, not because they are interesting — they show what a well-handled ticket looks like when nothing dramatic happens.

# 7.1 What the record looks like

The form below is a live incident mid-flight. Every field an analyst is expected to maintain is visible on it, and the fields written by the AI Suggested Response pilot are grouped in the middle band — see Section 11 for what they mean and how far to trust them.

Incident INC0010023 — ServiceNow

Save | Resolve | Delete

Number: INC0010023
Caller: Mariam Fouad
Category: Network
Subcategory: VPN
Business service: corporate-vpn
State: In Progress
Impact: 3 - Low
Urgency: 2 - Medium
Priority: 3 - Moderate
Assignment group: Service Desk Tier 1
Short description: Cannot connect to VPN since password reset this morning
AI Status: suggested
AI Confidence: 0.79
Human Review Required: true
AI Suggested Response: 1. Confirm with the user that they changed their password within the last 24 hours. 2. Ask the user to sign out of the VPN client completely, including the tray icon. 3. Clear the cached credential for the VPN profile. 4. Reconnect using the new password. Source: KB0001 - VPN authentication fails after a password change (v2).

INC0010023 with a drafted suggestion awaiting review. Human Review Required is set; nothing has been sent to the requester.

# 7.2 INC0010023 — VPN authentication failure after a password reset

Number: INC0010023
Caller: Mariam Fouad, Finance
Category: Network · VPN
Impact / Urgency: 3 – Individual / 2 – Medium
Assignment group: Service Desk Tier 1
Article applied: KB0001 v2
Resolution code: Resolved by knowledge article
Opened: 08 Sep 2026 09:14 GST
Channel: Self-service portal
Service: corporate-vpn
Priority: P3 – Moderate
Assignee: O. Sabry
Closed: 08 Sep 2026 10:02 GST
Breach: None · 48 min against a 3-day target

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>09:14</td><td>System</td><td>Incident created from the portal. Category set by requester to Network.</td></tr><tr><td>09:14</td><td>System</td><td>AI Suggested Response drafted. Confidence 0.79. Source KB0001 v2, section Resolution. Human Review Required set.</td></tr><tr><td>09:21</td><td>Work note</td><td>Picked up. Read the drafted suggestion; it matches the reported symptom. Confirmed with the caller by phone that the password was reset this morning.</td></tr></table>

Edition 4.0

**25** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>09:26</td><td>Work note</td><td>Applied KB0001 v2. Caller signed out of the VPN client including the tray icon, cleared the cached credential for the profile.</td></tr><tr><td>09:34</td><td>Comment</td><td>Hello Mariam — please try connecting again now with your new password and let me know how you get on.</td></tr><tr><td>09:48</td><td>Work note</td><td>Caller reports the connection now succeeds. Asked her to reconnect a second time to confirm the cached credential did not return.</td></tr><tr><td>09:57</td><td>Work note</td><td>Second connection successful. Checked the account is not locked in the identity console — it is not, so no follow-on to KB0005 needed.</td></tr><tr><td>10:02</td><td>Comment</td><td>Glad that worked. I have resolved this. If it recurs after your next password change, reopen this ticket and quote KB0001.</td></tr></table>

# Why this one is worth reading twice

The analyst read the drafted suggestion, verified the premise with the caller before applying it, and then checked the adjacent failure mode — a locked account — before closing. The whole thing took 48 minutes against a three-day target, and the journal is legible to someone who was not there.

# 7.3 INC0010047 — a fault with no article behind it

Number: INC0010047
Opened: 08 Sep 2026 11:32 GST
Caller: Facilities — meeting room 4
Channel: Telephone
Category: Hardware · Printer
Service: print-services
Impact / Urgency: 3 – Individual / 3 – Low
Priority: P4 – Low
Assignment group: Service Desk Tier 1 → Facilities
Assignee: Facilities duty manager
Article applied: None — no relevant article
Closed: 09 Sep 2026 14:20 GST
Resolution code: Referred to Facilities
Breach: None

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>11:32</td><td>System</td><td>Incident created from a telephone call.</td></tr><tr><td>11:33</td><td>System</td><td>AI Suggested Response: no matching knowledge article found. Best match KB0004 scored 0.31 against a threshold of 0.55. No resolution drafted. Flagged for human review.</td></tr><tr><td>11:40</td><td>Work note</td><td>Grinding noise on paper feed. This is a mechanical fault, not a queue fault, so KB0004 does not apply — the pilot was right to decline.</td></tr><tr><td>11:44</td><td>Work note</td><td>Checked with the caller that jobs are not merely queued. The device makes the noise with the queue empty.</td></tr><tr><td>11:51</td><td>Comment</td><td>Thanks for reporting this. The noise you describe is a mechanical fault on the device itself, which Facilities handle rather than IT. I have passed it to them with your details.</td></tr><tr><td>11:52</td><td>Work note</td><td>Referred to Facilities duty manager. Advised the room be marked out of use for printing until inspected.</td></tr></table>

Edition 4.0

**26** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>14:20</td><td>Work note</td><td>Facilities confirm the feed roller has been replaced. Caller confirms normal printing. Closed.</td></tr></table>

# This is the intended outcome when the knowledge base holds nothing relevant. The pilot declined to draft rather than producing a plausible printer procedure, the analyst confirmed the distinction between a mechanical fault and a queue fault, and the incident left IT within twenty minutes.

# 7.4 INC0010064 — three symptoms, one cause

Number: INC0010064
Opened: 09 Sep 2026 08:04 GST
Caller: Ahmed Zaki, Procurement
Channel: Telephone
Category: Inquiry → Identity
Service: identity
Impact / Urgency: 3 – Individual / 1 – High
Priority: P2 – High
Assignment group: Service Desk Tier 1
Assignee: O. Sabry
Article applied: KB0005 v4
Closed: 09 Sep 2026 09:11 GST
Resolution code: Resolved by knowledge article
Related: PRB0040012

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>08:04</td><td>Work note</td><td>Caller reports no email, shared drive gone, and Teams repeatedly asking him to sign in. Three services, one morning.</td></tr><tr><td>08:06</td><td>System</td><td>AI Suggested Response: three distinct symptoms detected across three services, no single article covers all three. No resolution drafted. Flagged for human review.</td></tr><tr><td>08:09</td><td>Work note</td><td>Simultaneous failures across email, file shares and collaboration is the KB0005 pattern, not three separate faults. Checked the identity console: account locked at 07:52.</td></tr><tr><td>08:14</td><td>Work note</td><td>Verified identity per procedure. Unlocked the account.</td></tr><tr><td>08:16</td><td>Work note</td><td>Account locked again within two minutes. A device is retrying an old credential. Lockout source shows the mobile mail profile.</td></tr><tr><td>08:31</td><td>Work note</td><td>Caller signed out of the corporate mail profile on his phone and re-entered the new password. Cleared cached credentials on the laptop including VPN.</td></tr><tr><td>08:44</td><td>Work note</td><td>Unlocked a second time. No further lockouts after 15 minutes of observation.</td></tr><tr><td>09:05</td><td>Work note</td><td>Confirmed access to email and the shared drive. Third lockout in six weeks for this caller — linked to PRB0040012.</td></tr><tr><td>09:11</td><td>Comment</td><td>All three problems had the same cause: your account had locked, which blocks everything at once. It is unlocked and your phone is no longer retrying the old password.</td></tr></table>

"Simultaneous failures across unrelated services are almost never several faults. They are one fault, one layer down."

Edition 4.0

**27** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 7.5 INC0010052 — escalated without being touched

Number: INC0010052
Opened: 08 Sep 2026 15:47 GST
Caller: Monitoring — order-processing
Channel: Alert
Category: Software · Application
Service: order-processing
Impact / Urgency: 1 – Enterprise / 1 – High
Priority: P1 – Critical
Assignment group: Platform Engineering
Assignee: K. Selim
Article applied: KB0010 v2
Related: PRB0040018, CHG0030455
Resolution code: Resolved by change
Breach: None · 2h 41m against a 4-hour target

<table><tr><th>TIME</th><th>TYPE</th><th>ENTRY</th></tr><tr><td>15:47</td><td>System</td><td>Incident raised from a monitoring alert. Pool saturation on order-processing. Priority derived P1 from Tier 1 criticality and alert severity.</td></tr><tr><td>15:47</td><td>System</td><td>AI Suggested Response: risk assessed as high before retrieval — Priority 1 on a Tier 1 service. No action taken. Escalated for human decision with the evidence attached.</td></tr><tr><td>15:49</td><td>Work note</td><td>Major incident bridge opened. Service owner paged.</td></tr><tr><td>15:52</td><td>Work note</td><td>Pool saturation confirmed on the metrics dashboard, not from the error text alone. Matches KB0010 v2 symptom exactly.</td></tr><tr><td>15:58</td><td>Work note</td><td><b>Restart explicitly ruled out</b> per KB0010 v2 step 1 and MIR-2026-03. Raising emergency change against CHG0030455 for the drain procedure.</td></tr><tr><td>16:24</td><td>Work note</td><td>Emergency change approved by change manager and service owner. Approval record attached — see 10.4.</td></tr><tr><td>16:41</td><td>Work note</td><td>Drain procedure applied. Pool utilisation falling. No orders dropped.</td></tr><tr><td>17:15</td><td>Work note</td><td>Thirty minutes of stable utilisation observed. Bridge stood down.</td></tr><tr><td>18:28</td><td>Work note</td><td>Resolved. Linked to PRB0040018, which remains open pending the permanent fix.</td></tr></table>

Edition 4.0
**28** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 8. Problems and known errors

# 8.1 The difference, and why it matters at the desk

A problem is the underlying cause of one or more incidents. A known error is a problem whose cause is understood and whose workaround is documented, but whose permanent fix has not yet shipped. The desk cares about the distinction for one reason: a known error has a workaround you may apply today, and a problem does not.

<table><tr><th></th><th>PROBLEM</th><th>KNOWN ERROR</th></tr><tr><td>Cause</td><td>Under investigation</td><td>Understood and recorded</td></tr><tr><td>Workaround</td><td>May not exist</td><td>Documented, and safe to apply</td></tr><tr><td>At the desk</td><td>Link the incident and escalate normally</td><td>Apply the workaround, link the incident, do not escalate</td></tr></table>

# 8.2 Open problem register

<table><tr><th>PROBLEM</th><th>DESCRIPTION</th><th>RAISED</th><th>INCIDENTS</th><th>OWNER AND STATUS</th></tr><tr><td>PRB0040012</td><td>Repeat account lockouts traced to cached credentials on mobile mail profiles. Affects roughly forty users a month, concentrated after the quarterly password rotation.</td><td>12 Feb 2026</td><td>63</td><td>Identity &amp; Access. Awaiting the modern-auth rollout, Q4.</td></tr><tr><td>PRB0040018</td><td>Connection pool exhaustion on order-processing under sustained load. Root cause is a long-running query path holding connections beyond their lifetime.</td><td>15 Mar 2026</td><td>9</td><td>Platform Engineering. Fix in test; target Q4.</td></tr><tr><td>PRB0040021</td><td>Wireless drops on the 5 GHz corporate SSID while roaming between access points on floors 3 and 4.</td><td>08 Jan 2026</td><td>41</td><td>Network Operations. Controller firmware scheduled, CHG0030588.</td></tr><tr><td>PRB0040026</td><td>Post-update endpoint degradation persisting beyond the expected 24-hour indexing window on a specific laptop model.</td><td>22 Jun 2026</td><td>17</td><td>Endpoint Engineering. Vendor case open.</td></tr><tr><td>PRB0040029</td><td>Print queues stalling on the third-floor device within an hour of a spooler restart.</td><td>30 Jul 2026</td><td>12</td><td>Print Services. Under investigation.</td></tr></table>

# 8.3 Known error register

Every entry here has a workaround you may apply without escalating. Link the incident to the known error so the count stays accurate — the counts are what fund the permanent fixes.

Edition 4.0
**29** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><th>KNOWN ERROR</th><th>SYMPTOM</th><th>WORKAROUND</th><th>PERMANENT FIX</th></tr><tr><td>KE0000034</td><td>SAP GUI times out with RFC_ERROR_COMMUNICATION from a known-good network.</td><td>The saved connection entry names a decommissioned application server. Change it to the message server and group. KB0008 step 3.</td><td>Connection profiles republished by SAP Basis. No date.</td></tr><tr><td>KE0000041</td><td>Mapped drive absent at sign-in although the server path opens when browsed directly.</td><td>Re-run the mapping script, or remap with reconnect-at-sign-in enabled. KB0003 step 2.</td><td>Login script rewrite, CHG0030602.</td></tr><tr><td>KE0000047</td><td>Account re-locks within minutes of an unlock, with no user action.</td><td>Identify the lockout source before the second unlock. Sign the mobile mail profile out first. KB0005 steps 3 and 6.</td><td>Modern-auth rollout under PRB0040012.</td></tr><tr><td>KE0000052</td><td>Wireless drops while walking between floors 3 and 4.</td><td>None for roaming users. For stationary users, disable adapter power saving. KB0009 step 2.</td><td>Controller firmware, CHG0030588.</td></tr><tr><td>KE0000055</td><td>Print queue stalls again within an hour of a spooler restart on the third-floor device.</td><td>Restart the queue on the print server rather than the client. Escalate on the second recurrence in a day.</td><td>Under investigation, PRB0040029.</td></tr></table>

Edition 4.0
30 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 9. Major incident report MIR-2026-03

Order processing unavailable, 14 March 2026, 40 minutes. Published 27 March 2026. This report is reproduced in full because three of its actions changed procedures that appear elsewhere in this manual.

# 9.1 Summary

Report: MIR-2026-03
Service: order-processing · Tier 1
Duration: 40 minutes, full unavailability
Incident manager: N. Abdelrahman
Orders affected: 312 in flight, 47 unrecoverable
Report author: D. Halim, Problem Manager
Declared: 14 Mar 2026 13:12 GST
Restored: 14 Mar 2026 13:52 GST
Incident: INC0009884
Service owner: K. Selim
Classification: Self-inflicted · procedural
Signed off: 27 Mar 2026, K. Selim

# 9.2 What happened

**The trigger.** At 12:58 the order service began returning HTTP 500 under normal midday load. Connection pool saturation was reported in the application log within a minute, and monitoring raised INC0009884 at 13:02 as a P1.

**The discovery.** Fulfilment noticed missing orders at 13:31. The bridge was reconvened, the gap was quantified at 47 unrecoverable orders, and manual recovery began.

**The response.** The analyst on the bridge searched the knowledge base, found KB0010, and applied it. Step 1 of that article, as it stood, instructed a restart of the application server.

**Restoration.** The service itself was never unavailable after 13:04. The forty minutes recorded above is the window in which orders were being accepted and lost, which is the number that matters commercially and the one this report uses.

**The consequence.** The restart cleared the pool as documented and simultaneously dropped every in-flight order. The service returned 200 within ninety seconds, so the restart looked successful on every signal the bridge was watching.

**The article was not wrong when it was written.**
KB0010 v1 was published in 2023 against an earlier
deployment where the service drained gracefully on
shutdown. That behaviour changed with the 2025
platform migration and the article was never revisited.

# 9.3 Timeline

<table><tr><th>TIME</th><th>ACTOR</th><th>EVENT</th></tr><tr><td>12:58</td><td>System</td><td>Order service begins returning HTTP 500 under load.</td></tr><tr><td>12:59</td><td>System</td><td>Pool saturation logged. Alert raised.</td></tr><tr><td>13:02</td><td>Monitoring</td><td>INC0009884 created. P1 derived from Tier 1 criticality.</td></tr><tr><td>13:04</td><td>Tier 1</td><td>Bridge opened. Service owner paged.</td></tr><tr><td>13:07</td><td>Tier 1</td><td>KB0010 located and read. Step 1 applied — application server restarted.</td></tr><tr><td>13:09</td><td>System</td><td>Service returns 200. Pool utilisation normal. Bridge stands down.</td></tr></table>

Edition 4.0

**31** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><th>TIME</th><th>ACTOR</th><th>EVENT</th></tr><tr><td>13:12</td><td>Incident mgr</td><td>Major incident declared retrospectively for the pool event.</td></tr><tr><td>13:31</td><td>Fulfilment</td><td>Missing orders reported. Bridge reconvened.</td></tr><tr><td>13:44</td><td>Platform Eng</td><td>Gap quantified: 312 in flight at restart, 47 unrecoverable.</td></tr><tr><td>13:52</td><td>Incident mgr</td><td>Manual recovery complete for the 265 recoverable orders. Service confirmed restored.</td></tr><tr><td>14:20</td><td>Knowledge</td><td>KB0010 v1 set to retired pending revision.</td></tr><tr><td>16:00</td><td>Change</td><td>CHG0030455 raised for a drain procedure that does not drop in-flight work.</td></tr></table>

# 9.4 The bridge whiteboard

The photograph below was taken in the Dubai operations room at 13:40, while the recovery was being scoped. It is included because the two boxed items on it became actions 1 and 4 below, and because it records the reasoning as it was at the time rather than as it was reconstructed afterwards.

Whiteboard — sprint 2 planning
EVENT FLOW
BR -> RESTMsg -> /events
202 FIRST!! then work
idem key = event_id NOT sys_id
claim: PATCH ai_status
before dispatch
NOTHING POLLS
if there is a loop reading
the incident table -> FAIL
OPEN Qs
- async BR? previous is null
- who owns the PDI
- threshold: 0.55 ??
- retire v1 of KB0010
DONE = 202 in <1s

The operations room whiteboard at 13:40 on 14 March. The boxed rule at the foot became action 4.

# 9.5 Root cause

1. **Immediate cause.** A restart of the order service application server dropped in-flight orders.
2. **Contributing cause.** The knowledge article instructing that restart was correct for a deployment that no longer existed, and had not been reviewed since the 2025 migration.
3. **Contributing cause.** No review was triggered by the migration itself. Articles are reviewed on a calendar cycle, and the migration changed behaviour that several articles depended on.
4. **Contributing cause.** The bridge's success signals — HTTP 200 and normal pool utilisation — did not include order continuity, so the loss was invisible for 22 minutes.

Edition 4.0

**32** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

“The article was well written, correct when published, and dangerous by the time it was applied. That is the ordinary shape of this failure, not an unusual one.”

# 9.6 Actions

<table><tr><th>#</th><th>ACTION</th><th>OWNER</th><th>DUE</th><th>STATUS</th></tr><tr><td>1</td><td>Revise KB0010. Version 1 retired, version 2 published with the restart explicitly ruled out and a change-controlled drain path substituted.</td><td>H. Moawad</td><td>02 Apr 2026</td><td>Closed</td></tr><tr><td>2</td><td>Build and test the pool drain procedure that recycles connections without dropping in-flight work. Register it as a pre-approved emergency change.</td><td>K. Selim</td><td>02 Apr 2026</td><td>Closed</td></tr><tr><td>3</td><td>Raise PRB0040018 for the underlying connection-lifetime defect and track the permanent fix separately from the workaround.</td><td>D. Halim</td><td>20 Mar 2026</td><td>Closed</td></tr><tr><td>4</td><td>Add a migration-triggered review to the knowledge lifecycle: any platform migration triggers a review of every article naming the affected service, regardless of the calendar cycle.</td><td>H. Moawad</td><td>30 Apr 2026</td><td>Closed</td></tr><tr><td>5</td><td>Add order continuity to the P1 restoration checklist for order-processing, so that a service returning 200 is not by itself treated as restored.</td><td>N. Abdelrahman</td><td>30 Apr 2026</td><td>Closed</td></tr><tr><td>6</td><td>Search the estate for saved copies of KB0010 v1 outside the knowledge base — wikis, chat pins, personal notes — and replace them.</td><td>O. Sabry</td><td>31 May 2026</td><td>Open</td></tr></table>

# Action 6 is still open, and it is the one that matters

Every other action changed a system. Action 6 is about the copies people kept, and there is no reliable way to close it. If you find KB0010 v1 anywhere — a saved PDF, a pinned message, a page in a team wiki — replace it with a link to version 2 and tell the knowledge manager where it was.

Edition 4.0

**33** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 10. Change management

# 10.1 What the desk needs to know

Most of change management happens elsewhere. Three parts of it reach the desk: recognising when a fix requires a change rather than an incident action, holding an incident correctly against a change window, and knowing which changes are pre-approved so you do not wait for an approval that is not needed.

<table><tr><th>TYPE</th><th>USE WHEN</th><th>APPROVAL</th><th>LEAD TIME</th></tr><tr><td>Standard</td><td>The change is pre-approved and its procedure is documented and unchanged.</td><td>None required</td><td>None — proceed</td></tr><tr><td>Normal</td><td>Planned work with an assessable risk and a rollback.</td><td>CAB, weekly</td><td>5 working days</td></tr><tr><td>Emergency</td><td>Required to restore or protect a Tier 1 or Tier 2 service now.</td><td>Change manager and service owner, both</td><td>Immediate</td></tr><tr><td>Latent</td><td>A change already applied under emergency conditions, recorded afterwards.</td><td>Retrospective, at the next CAB</td><td>Within 2 working days</td></tr></table>

# 10.2 Pre-approved standard changes

<table><tr><th>CHANGE</th><th>PROCEDURE</th><th>WHO MAY APPLY IT</th><th>RECORDS TO LINK</th></tr><tr><td>CHG0030401</td><td>Print spooler restart and queue clear on a client workstation. KB0004.</td><td>Tier 1 analyst</td><td>The incident</td></tr><tr><td>CHG0030418</td><td>Cached credential clear for the VPN profile. KB0001.</td><td>Tier 1 analyst</td><td>The incident</td></tr><tr><td>CHG0030422</td><td>Account unlock following identity verification. KB0005.</td><td>Tier 1 analyst, IAM</td><td>The incident, PRB0040012</td></tr><tr><td>CHG0030455</td><td>Order service pool drain. Recycles connections without dropping in-flight work. KB0010 v2.</td><td><strong>Platform Engineering only</strong>, after emergency approval</td><td>The incident, PRB0040018</td></tr><tr><td>CHG0030470</td><td>Wireless adapter driver update on a single endpoint. KB0009.</td><td>Endpoint Engineering</td><td>The incident, PRB0040021</td></tr></table>

# CHG0030455 is pre-approved as a procedure, not as an action

The drain procedure itself needs no re-assessment — that is what pre-approval buys. Executing it against a live Tier 1 service still requires the change manager and the service owner to approve, on the record, before it runs. Pre-approval removes the design review, not the authorisation.

# 10.3 Emergency approval

1. The engineer proposing the action states the service, the procedure, the expected effect and the rollback, in the incident journal.

Edition 4.0							34 of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

2. The change manager confirms the procedure matches a documented one and that the rollback is real.

3. The service owner confirms the business impact is acceptable now rather than at the next window.

4. **Both approvals are recorded before the action runs.** An action taken first and approved afterwards is a latent change and is reported as a deviation.

5. The approval record is attached to the incident and referenced in the change.

# 10.4 Approval record — CHG0030455 against INC0010052

The record below is the one referenced from the journal in 7.5. It is reproduced as the form is completed and stored: the evidence, the risk verdict and the approval on a single sheet.

HIGH-RISK ACTION — APPROVAL RECORD

Incident: INC0010052
Execution ID: 7c1f8a02e4021bd9
Risk level: HIGH
Requested action: propose_resolution
Evidence: KB0010 v2, section Resolution, score 0.88
Approver decision:
    [x] Approved
    [ ] Approved with edits
    [ ] Rejected

Approver: N. Abdelrahman
Role: Service Delivery Manager
Date: 08 / 09 / 2026
Time: 09:41
APPROVED
08 SEP 2026

Emergency approval for the pool drain against INC0010052. Recorded at 16:24, seventeen minutes before the procedure ran.

Edition 4.0

**35** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 11. The AI Suggested Response pilot

A pilot service that drafts a candidate resolution on eligible incidents, cites the article it came from, and flags the incident for human review. It never resolves, closes or reassigns anything. This section is what the desk needs in order to work alongside it.

# 11.1 What it does, and what it may not do

When an eligible incident is created, the pilot reads it, searches the published knowledge base, and — if it finds evidence strong enough — writes a numbered procedure into AI Suggested Response together with the article it came from. It then sets Human Review Required and stops. An analyst reads the draft and applies it, edits it or discards it. Nothing reaches the requester unless a person puts it there.

<table><tr><th>IT MAY</th><th>IT MAY NOT</th></tr><tr><td>▪ Read an incident and its category ▪ Search published articles only ▪ Write a draft into AI Suggested Response ▪ Write an internal work note ▪ Set Human Review Required and a confidence value ▪ Decline to answer, and say why</td><td>▪ Resolve, close or cancel an incident ▪ Reassign it to another group ▪ Write to Additional comments, ever ▪ Email or otherwise contact the requester ▪ Read a draft or retired article ▪ Act on a Priority 1 or a Tier 1 service without approval</td></tr><tr><td>These capabilities exist in the service.</td><td>These capabilities do not exist in the service. They are absent, not disabled — there is no setting that enables them.</td></tr></table>

Edition 4.0

**36** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 11.2 How a suggestion is produced

# The system, end to end

One event in, one grounded suggestion out. Nothing polls; nothing auto-resolves.

1 · TICKETING SYSTEM
Incident raised
A requester reports a symptom. Category, service and text captured.
Business Rule
Fires on insert and on relevant update. Checks eligibility.
RESTMessageV2
Posts event_id, sys_id, number, event_type. Never the record.
Incident form
AI Suggested Response and Human Review Required render here.
minimal event over HTTPS
2 · PILOT SERVICE — THE FRONT DOOR
POST /events
Authenticate the caller. Validate with Pydantic. 401 / 422 on failure.
Idempotency + claim
Persist the event key. A replay is discarded. Claim the incident.
202 Accepted
Returned before any model runs. ServiceNow is never blocked.
Dispatch
Queued to a worker. The desk is never blocked by it.
write-back
dispatched to the worker
3 · REASONING — RETRIEVE, GROUND, DECIDE
Retrieve
Published articles only, filtered by service and category.
Reason
Reason over the retrieved articles. Nothing else.
Ground + check
Numbered procedure, cited article. Score, risk and confidence gates.
Write back
Table API: suggestion, confidence, work note, Human Review Required.
4 · Evidence — every run leaves a record
The run log holds every step: what was searched, what was found, what was drafted and what was blocked.
The execution record holds the attempt itself — including attempts that produced nothing at all.
Acceptance is measured monthly: drafts applied or edited, against drafts written. See Section 12.

The pilot end to end. The teal path is the write-back onto the incident form; a person always stands between the draft and the requester.

Nothing polls the ticketing system. A business rule on the incident table emits an event when an incident becomes eligible, and the pilot responds to that event. If the pilot is unavailable, incidents are created and worked exactly as they were before it existed — the desk is never blocked by it.

Edition 4.0

**37** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# One event, one run

The webhook answers first and reasons afterwards. A replay is answered too — and then dropped.

Ticketing system
Pilot API
Store
Worker
FIRST EVENT
POST /events
event_id, sys_id, number, event_type
put(event_id)
new key stored
PATCH ai_status = in_progress
the incident is claimed
202 Accepted
under one second, no model called
dispatch(event)
queued to a worker
PATCH suggestion + human_review
write-back after retrieval and generation
REPLAY OF THE SAME EVENT
POST /events
identical event_id
put(event_id) — already present
Outcome of the replay
202 Accepted is returned again — the caller must not see an error — but nothing is dispatched and no second suggestion is written.

The event exchange. The pilot acknowledges within a second and reasons afterwards, so a slow answer never delays a form save.

# 11.3 Eligibility

Not every incident is offered to the pilot. The checks below are applied inside the ticketing system before any event leaves it.

<table><tr><th>LAYER</th><th>CHECK</th><th>WHY</th></tr><tr><td rowspan="4">Ticketing system</td><td>Incident is active</td><td>Resolved and closed incidents are finished; nothing the pilot writes improves them.</td></tr><tr><td>Category is in the supported set</td><td>The corpus covers network, software, hardware and inquiry. Anything else would produce a refusal at best.</td></tr><tr><td>Not already processed</td><td>Prevents the write-back re-triggering the rule that caused it.</td></tr><tr><td>Not an AI-field-only update</td><td>Stops an unrelated field change from re-running the pilot.</td></tr><tr><td rowspan="3">Pilot service</td><td>The event is authentic</td><td>Signed. An unsigned event is rejected without being read.</td></tr><tr><td>The event has not been seen before</td><td>A retried delivery produces no second suggestion.</td></tr><tr><td>The incident is still eligible on re-read</td><td>An incident an analyst has taken over is left alone.</td></tr><tr><td rowspan="2">Desk override</td><td><i>AI assistance</i> unticked on the incident</td><td>Any analyst may exclude an individual incident. That decision is final and is never overridden.</td></tr><tr><td>The incident is on hold</td><td>Something is deliberately waiting. The pilot does not add noise to it.</td></tr></table>

Edition 4.0

**38** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 11.4 Three gates, and what each outcome means to you

# The decision ladder

Three gates stand between a retrieved chunk and a written suggestion. Any one of them can stop the run.

GATE 1
Evidence
Did any chunk clear the score threshold?
GATE 2
Risk
Is this a high-risk category, service or action?
GATE 3
Confidence
Is the derived confidence above the floor?
OUTCOMES
Suggest
All three gates pass. A numbered, cited procedure is written to AI Suggested Response. Human Review Required is set. A service desk agent still approves, edits or rejects it.
Escalate to a human
Risk is high, or confidence sits below the floor. No draft is written. A work note records the risk verdict and the evidence gathered, and the incident waits for a person.
Refuse and hand off
No chunk cleared the score threshold, so no fix is drafted. An explicit note says the knowledge base holds nothing relevant. Silence is the correct answer here.

The three gates. A draft is written only when all three pass; otherwise the incident is flagged and left for a person.

<table><tr><th>OUTCOME</th><th>WHAT YOU SEE ON THE FORM</th><th>WHAT TO DO</th></tr><tr><td>Suggested</td><td>A numbered procedure with a cited article. Confidence between 0 and 1.</td><td>Read it against the reported symptom before applying it. If it does not match, discard it and say so in the work note — that note is what improves the corpus.</td></tr><tr><td>Escalated — no evidence</td><td>No draft. A work note naming what was searched and the best score found.</td><td>Work the incident normally. The absence of an article is itself useful: if the fault recurs, propose one.</td></tr><tr><td>Escalated — high risk</td><td>No draft. A work note recording the risk verdict and the evidence that was gathered.</td><td>Follow Sections 4 and 10. The pilot has deliberately not acted; it has not failed.</td></tr><tr><td>Escalated — low confidence</td><td>No draft, or a draft marked below the confidence floor.</td><td>Treat as if there were no draft. Do not apply a below-floor draft because it looks plausible.</td></tr><tr><td>Failed</td><td>AI Status shows failed, with a reason.</td><td>Nothing. Work the incident normally and, if it repeats on the same category, raise it with the service owner.</td></tr></table>

# A declined suggestion is a correct outcome, not a fault

The pilot declines on roughly one incident in five. That is the design working: the alternative is a confident printer procedure for a mechanical fault, carrying our citation format and a confidence score. INC0010047 in 7.3 is the reference case.

Edition 4.0

**39** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 11.5 Where the drafts come from

# The retrieval pipeline

Build time runs once per corpus change. Query time runs once per incident.

BUILD TIME · ONE COMMAND, REPEATABLE
Load
Pull published knowledge articles from the KB.
Chunk
Fixed size with overlap. Article identity kept.
Embed
Vectors built once per corpus change.
Index
Persisted Qdrant collection with payload metadata.
QUERY TIME · ONCE PER INCIDENT
Query
Short description plus the symptom text. No credentials, no PII.
Filter
state = published, category, service, current version only.
Search
Ranked candidates, each with a score against the query.
Rerank
Reorder the candidates before anything reaches the drafting step.
Output: top-k chunks, each with a similarity score and the article number it came from.
Only published articles at their current version are candidates. Draft and retired revisions are removed before ranking, not ranked low.
How the knowledge base is indexed and searched. Only published articles at their current version are ever candidates.

The retrieval console shows what the pilot found for a given incident, with the score for each candidate. It is the first place to look when a suggestion is wrong: nine times in ten the draft is a faithful reading of the wrong article, not an invention.

<table><tr><th>RANK</th><th>ARTICLE</th><th>SECTION</th><th>SCORE</th><th>READING</th></tr><tr><td>1</td><td>KB0001</td><td>Resolution</td><td>0.847</td><td>Correct article and section.</td></tr><tr><td>2</td><td>KB0001</td><td>Cause</td><td>0.812</td><td>Same article, adjacent section.</td></tr><tr><td>3</td><td>KB0005</td><td>Symptom</td><td>0.694</td><td>Plausible neighbour, not wrong.</td></tr><tr><td>4</td><td>KB0009</td><td>Resolution</td><td>0.611</td><td>Category-adjacent noise.</td></tr><tr><td>5</td><td>KB0003</td><td>Symptom</td><td>0.585</td><td>Noise.</td></tr></table>

Threshold 0.55 · top-3 hit: yes · confidence 0.79

Retrieval console output for INC0010023. KB0001 at rank 1 and rank 2, with adjacent articles below the useful line.

Typed out, so the numbers can be quoted in a journal entry:

<table><tr><th>RANK</th><th>ARTICLE</th><th>SECTION</th><th>SCORE</th><th>READING</th></tr><tr><td>1</td><td>KB0001</td><td>Resolution</td><td>0.847</td><td>Correct article, correct section. This is what a good run looks like.</td></tr><tr><td>2</td><td>KB0001</td><td>Cause</td><td>0.812</td><td>Same article, adjacent section. Expected and useful.</td></tr><tr><td>3</td><td>KB0005</td><td>Symptom</td><td>0.694</td><td>Account lockout. A plausible neighbour — password changes cause both.</td></tr><tr><td>4</td><td>KB0009</td><td>Resolution</td><td>0.611</td><td>Wireless. Category-adjacent noise.</td></tr><tr><td>5</td><td>KB0003</td><td>Symptom</td><td>0.585</td><td>Shared drive mapping. Noise.</td></tr></table>

Edition 4.0

**40** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 11.6 Safety controls

Two stages of deterministic checks sit around the model. They are code, not instructions, and the model cannot route around them.

<table><tr><th>STAGE</th><th>CHECKS PERFORMED</th><th>ENFORCEMENT</th></tr><tr><td>Before</td><td>▪ Screening for instructions embedded in the incident text ▪ Credential and key redaction ▪ Personal-data redaction ▪ Length and encoding bounds</td><td>Runs before the model is called. A flagged incident is routed to a person and never reaches drafting.</td></tr><tr><td>After</td><td>1. Validate the draft against the expected shape 2. Match every step back to a retrieved article 3. Enforce the permitted-action list 4. Scan the text for secrets before writing</td><td>Runs after drafting and before the write. A block is logged and escalated — never logged and continued.</td></tr><tr><td>Permitted actions</td><td>read_incident — read search_knowledge — read write_work_note — low risk flag_human_review — low risk write_ai_fields — low risk</td><td>Checked at call time. Resolve, close, reassign and contact-requester are not on the list and do not exist in the service.</td></tr></table>

If an incident description contains text addressed to the pilot rather than describing a fault — which has happened twice during the pilot — the screening stage flags it, the incident goes to a person, and the genuine symptom underneath is still worked normally. Report any occurrence to Security Operations as well as to the pilot owner.

# 11.7 Configuration

The values below are the ones in effect for edition 4.0. They are held in the pilot's configuration file and are changed under normal change control, one at a time, with the effect measured before the next change.

<table><tr><th>GROUP</th><th>VALUES</th><th>WHY IT IS SET THIS WAY</th></tr><tr><td>Indexing</td><td><table><tr><th>KEY</th><th>VALUE</th></tr><tr><td>chunk_size</td><td>700</td></tr><tr><td>overlap</td><td>120</td></tr><tr><td>split_on</td><td>heading</td></tr></table></td><td>Splitting on headings keeps Symptom, Cause and Resolution intact. Fixed-width splitting cuts procedures mid-step, and the damage only shows when a draft is missing its second half.</td></tr><tr><td>Search</td><td><table><tr><th>KEY</th><th>VALUE</th></tr><tr><td>top_k</td><td>5</td></tr><tr><td>threshold</td><td>0.55</td></tr><tr><td>published</td><td>true</td></tr></table></td><td>The threshold sits between the scores seen on answerable incidents and those seen on out-of-scope ones. Published-only is a hard filter — it is what keeps KB0010 v1 out of a live incident.</td></tr><tr><td>Safety</td><td><table><tr><th>KEY</th><th>VALUE</th></tr><tr><td>floor</td><td>0.45</td></tr><tr><td>risk_p</td><td>[1]</td></tr><tr><td>retries</td><td>1</td></tr></table></td><td>Below the floor the pilot escalates rather than drafting. Priority 1 leaves the automated path before any search runs, so nothing is spent on an incident that was never eligible.</td></tr></table>

Edition 4.0

**41** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

Config excerpt (photographed from a laptop screen)
retrieval:
  collection:      kb_articles
  chunk_size:      700
  chunk_overlap:   120
  top_k:           5
  score_threshold: 0.55
  published_only:  true
  hybrid:          false
safety:
  confidence_floor: 0.45
  high_risk_services: [payments, identity, order-processing]

The capture beside this paragraph circulated during the pilot handover and is reproduced because several teams copied their values from it rather than from the file. Two of the numbers in it are now out of date. Read the configuration from the repository, not from a photograph of somebody's screen — a value nobody can diff is a value nobody can review, and the whole point of holding these in one file is that a change to any of them shows up in the change record.

# 11.8 Reading the run log

Every run is logged. The extract below covers one incident from acceptance to write-back and is the level of detail available when a suggestion needs explaining.

<table><tr><td>2026-09-08 09:14:22</td><td>INFO</td><td>webhook</td><td>event 9f2b1c4e accepted sys_id=1c741bd7 -&gt; 202</td></tr><tr><td>2026-09-08 09:14:22</td><td>INFO</td><td>idem</td><td>claim ok event_id=9f2b1c4e</td></tr><tr><td>2026-09-08 09:14:23</td><td>INFO</td><td>snow</td><td>PATCH incident u_ai_status=in_progress (204)</td></tr><tr><td>2026-09-08 09:14:24</td><td>INFO</td><td>retrv</td><td>top_k=5 filter=state:published,category:network</td></tr><tr><td>2026-09-08 09:14:24</td><td>INFO</td><td>retrv</td><td>hit#1 KB0001/Resolution score=0.847</td></tr><tr><td>2026-09-08 09:14:24</td><td>INFO</td><td>retrv</td><td>hit#2 KB0001/Cause score=0.812</td></tr><tr><td>2026-09-08 09:14:24</td><td>INFO</td><td>retrv</td><td>hit#3 KB0005/Symptom score=0.694</td></tr><tr><td>2026-09-08 09:14:29</td><td>INFO</td><td>gen</td><td>prompt_version=v3 tokens_in=1842 tokens_out=214</td></tr><tr><td>2026-09-08 09:14:29</td><td>WARN</td><td>gen</td><td>step 5 not matched to a chunk - dropped</td></tr><tr><td>2026-09-08 09:14:30</td><td>INFO</td><td>conf</td><td>confidence=0.79 threshold=0.55 gate=pass</td></tr><tr><td>2026-09-08 09:14:31</td><td>INFO</td><td>snow</td><td>PATCH suggestion + human_review=true (204)</td></tr><tr><td>2026-09-08 09:14:31</td><td>ERROR</td><td>trace</td><td>langfuse flush timeout after 3000ms - retrying</td></tr><tr><td>2026-09-08 09:14:33</td><td>INFO</td><td>trace</td><td>flush ok trace_id=7c1f8a02e4021bd9</td></tr><tr><td>2026-09-08 09:14:33</td><td>INFO</td><td>worker</td><td>run complete in 11.2s</td></tr></table>

One complete run. The WARN line at 09:14:29 is the evidence check removing a drafted step that matched no article.

# 11.9 Integration payload

For reference during supplier conversations: the event the ticketing system sends carries identifiers only. No incident text, no requester details and no attachments leave the platform in the event itself — the pilot reads what it is authorised to read, with its own credentials.

Edition 4.0

**42** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

Event payload — as pasted into the ticket

POST /api/v1/events/servicenow
X-Signature: 4a7f19c2be08d5316f0a2c9d7e41b8a3

{
  "event_id":  "9f2b1c4e7a5d4f8bb1e3c07d2a6f4e19",
  "sys_id":    "1c741bd70b2322007518478d83673af3",
  "number":    "INC0010023",
  "event_type": "incident.created",
  "emitted_at": "2026-09-08 09:14:22"
}

The event payload, as attached to a supplier ticket during integration testing and reproduced as received.

Edition 4.0

**43** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# 12. Reporting

# 12.1 The monthly service review

Held on the second Tuesday. Attended by the service delivery manager, the team leads, the problem manager and any service owner with an open action. The pack is circulated two working days ahead and is not presented in the meeting — the meeting is for the exceptions.

<table><tr><th>MEASURE</th><th>DEFINITION</th><th>WHAT IT IS USED FOR</th></tr><tr><td>Volume</td><td>Incidents created in the period, by priority and by service.</td><td>Capacity planning. A rise with no matching change is the first sign of an undiagnosed problem.</td></tr><tr><td>First-contact resolution</td><td>Resolved by Tier 1 without escalation, as a share of all incidents.</td><td>Knowledge coverage. This is the number that moves when Section 6 improves.</td></tr><tr><td>SLA attainment</td><td>Incidents meeting both response and resolution targets, by priority.</td><td>The contractual measure. Reported with the breaches listed individually, never as a percentage alone.</td></tr><tr><td>Article usage</td><td>Incidents naming an article in the journal, by article.</td><td>Which articles earn their place at the next review. An article with no uses in twelve months is a candidate for retirement.</td></tr><tr><td>Reopen rate</td><td>Incidents reopened within five working days of resolution.</td><td>Resolution quality. A rising reopen rate alongside improving SLA attainment means we are closing tickets, not fixing faults.</td></tr><tr><td>Pilot acceptance</td><td>Drafts applied or edited by an analyst, as a share of drafts written.</td><td>Whether the pilot is helping. A draft discarded is not a failure; a draft nobody reads is.</td></tr></table>

# 12.2 Reporting to service delivery managers

Service delivery managers at BARQ read their operational reporting in Arabic. The layout below is the agreed one: labels right-aligned in Arabic, and identifiers, scores and article numbers left exactly as they appear in the platform.

رقم البلاغ: INC0010023
الحالة: مقترح — بانتظار مراجعة بشرية
درجة الثقة: 0.79
المقال المرجعي: KB0001 (v2)
زمن المعالجة: 11.2 ثانية
النتيجة: تم كتابة الاقتراح في ملاحظات العمل
SmartOps · Run report
تقرير تشغيل النظام
ملاحظة: النظام يقترح ولا يغلق البلاغ.
القرار النهائي لموظف الدعم دائمًا.

A run report as delivered. Identifiers are never translated or transliterated — they must remain resolvable in the ticketing system.

Edition 4.0

**44** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Never translate an identifier

INC0010023, KB0001 and CHG0030455 are keys, not words. Translating or transliterating one breaks the link between a report and the record it describes, and that link is the only thing that makes a report auditable.

# 12.3 What is not reported

▪ **Individual analyst performance.** The pack is service-level. Individual coaching happens between an analyst and their team lead, from data neither of them publishes.

▪ **Raw journal text.** Journals contain requester details. Extracts quoted in a review are anonymised first.

▪ **Absolute pilot confidence values across periods.** The value is derived from search scores, and those are not comparable if the underlying model changes. Report the distribution and the acceptance rate instead.

Edition 4.0

**45** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Appendix A · Glossary

<table><tr><th>TERM</th><th>MEANING AT BARQ</th></tr><tr><td>Bridge</td><td>The conference line opened for a P1 or a multi-service P2. Attendance is the incident manager, the affected service owners and one representative per resolver group. It stays open until the service is restored, not until the cause is understood.</td></tr><tr><td>Breach</td><td>A response or resolution target missed. Recorded automatically and not editable. A breach with a good reason is still a breach; the reason belongs in the journal.</td></tr><tr><td>Clock</td><td>The elapsed or working-hours measure against a target. Starts at creation, pauses on hold, stops at resolved.</td></tr><tr><td>Criticality</td><td>The tier assigned to a service in Section 5. Feeds the impact column of the priority matrix and is changed only through governance.</td></tr><tr><td>Deviation</td><td>A departure from a Must step in this manual. Recorded in the journal at the time, and reviewed at the monthly service review.</td></tr><tr><td>First-contact resolution</td><td>Resolved by Tier 1 with no escalation. Not the same as resolved on the first call.</td></tr><tr><td>Known error</td><td>A problem whose cause is understood and whose workaround is documented. See 8.3.</td></tr><tr><td>Latent change</td><td>A change applied under emergency conditions and recorded afterwards. Reported as a deviation.</td></tr><tr><td>Major incident</td><td>A declared state, not a priority. Declared by the incident manager; it may accompany a P1 or a multi-service P2.</td></tr><tr><td>No-contact rule</td><td>Resolution after two recorded chases at least one working day apart with no requester response. See 3.5.</td></tr><tr><td>Problem</td><td>The underlying cause of one or more incidents. Not necessarily understood yet. See 8.1.</td></tr><tr><td>Reopen</td><td>A resolved incident returned to In Progress within five working days. Counted in Section 12.</td></tr><tr><td>Resolver group</td><td>A team that accepts escalations within its service scope. Listed in 4.2 and in the service catalogue.</td></tr><tr><td>Service owner</td><td>Accountable for a service in Section 5. Approves emergency changes against it and signs post-incident reviews.</td></tr><tr><td>Standard change</td><td>A pre-approved change whose procedure is documented and unchanged. Listed in 10.2.</td></tr><tr><td>Suggested response</td><td>A draft written by the pilot in Section 11. A candidate, never an action.</td></tr><tr><td>Workaround</td><td>A documented way to restore service without fixing the cause. Applying one resolves the incident and leaves the problem open.</td></tr></table>

Edition 4.0

**46** of 52 ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Appendix B · Templates

# B.1 Escalation handover

Paste into the work note when escalating. A resolver group may reject an escalation that does not carry these five things.

ESCALATION — <group>

Symptom     : what the requester reports, in their words
Scope       : one user / a team / a floor / the whole service
Started     : when, and what changed at that time
Checked     : what you tried, with the article numbers
Ruled out   : what it is not, and how you know
Requester   : name, location, contactable until
Priority    : P<n>, derived from impact <n> and urgency <n>

# B.2 Requester update

For Additional comments. Three sentences: what is happening, what you need, when they will next hear from you.

Hello <name>,

<What is happening, in their language. No host names, no group names.>
<What you need from them, if anything, and by when.>
<When they will hear from you next. Give a time, not "shortly".>

<Your name>
BARQ Service Desk · <incident number>

# B.3 No-contact resolution

Hello <name>,

I have tried to reach you twice about this and have not been able to, so I am closing it for now. Nothing is lost — reply to this message or quote <incident number> and it will reopen with its history intact.

<Your name>
BARQ Service Desk · <incident number>

# B.4 Knowledge article proposal

Raise against the *Knowledge — new article* catalogue item. The knowledge manager will not publish a proposal that omits the cause; a procedure with no cause behind it is a habit, not knowledge.

<table><tr><td>Title</td><td>The symptom as a requester would report it, not the fix</td></tr><tr><td>Service and category</td><td>From the catalogue in Section 5</td></tr><tr><td>Symptom</td><td>What the requester sees. Include the exact error text if there is one</td></tr><tr><td>Cause</td><td>Why it happens. Required — a proposal without this is returned</td></tr><tr><td>Resolution</td><td>Numbered steps, each a single action a Tier 1 analyst can perform</td></tr></table>

Edition 4.0

**47** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><td>Escalation</td><td>When to stop and who to send it to</td></tr><tr><td>Evidence</td><td>At least two incident numbers where this pattern occurred</td></tr></table>

Edition 4.0                                                                                                    **48** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Appendix C · Impact and urgency worksheet

Use this when the priority is disputed. Answer both columns, then read the priority off the matrix in 3.3.

<table><tr><th>IMPACT — HOW MANY, AND HOW BADLY</th><th>URGENCY — HOW FAST IT DEGRADES</th></tr><tr><td>▪ <b>1 – Enterprise.</b> A whole service is unavailable, or an entire site is affected. ▪ <b>2 – Department.</b> A team, a floor or a business function cannot work. ▪ <b>3 – Individual.</b> One person is affected, or a small number with a workaround.</td><td>▪ <b>1 – High.</b> Work stops now, or a deadline inside four hours is at risk. ▪ <b>2 – Medium.</b> Work is degraded but continuing, or a deadline today is at risk. ▪ <b>3 – Low.</b> Inconvenient. A workaround exists and is acceptable for now.</td></tr><tr><td>Count people who <b>cannot work</b>, not people who noticed.</td><td>Urgency is about the rate of harm, not about who is asking.</td></tr></table>

# The two questions that settle most disputes

Is there a workaround the requester can use today? If yes, urgency is rarely 1, whatever the pressure on the call.
Would a second person report this independently? If yes, impact is rarely 3, even though only one person has called.

Edition 4.0                                                                                                                                                                 **49** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Appendix D · Directory

Names and roles as at the edition date. Extension numbers and the on-call rota are maintained in the ticketing system and are authoritative there; this table is for routing, not for dialling.

<table><tr><th>GROUP</th><th>ROLE</th><th>NAME</th><th>COVERS</th><th>OUT OF HOURS</th></tr><tr><td rowspan="5">Service management</td><td>Service Delivery Manager</td><td>N. Abdelrahman</td><td>All services</td><td>On-call rota</td></tr><tr><td>Service Desk Team Lead</td><td>O. Sabry</td><td>Tier 1, both sites</td><td>Extended hours</td></tr><tr><td>Problem Manager</td><td>D. Halim</td><td>Problem and known error registers</td><td>Working hours</td></tr><tr><td>Knowledge Manager</td><td>H. Moawad</td><td>Section 6, article lifecycle</td><td>Working hours</td></tr><tr><td>Change Manager</td><td>Y. Naguib</td><td>CAB, emergency approvals</td><td>On-call rota</td></tr><tr><td rowspan="5">Service owners</td><td>Head of Platform Engineering</td><td>K. Selim</td><td>order-processing, sap-erp</td><td>On-call rota</td></tr><tr><td>Network Operations Lead</td><td>L. Haddad</td><td>corporate-vpn, corporate-wifi, file-services</td><td>On-call rota</td></tr><tr><td>Identity &amp; Access Lead</td><td>N. Abdelrahman</td><td>identity</td><td>On-call rota</td></tr><tr><td>Collaboration Services Lead</td><td>O. Sabry</td><td>corporate-email</td><td>Extended hours</td></tr><tr><td>Endpoint Engineering Lead</td><td>D. Halim</td><td>endpoint, print-services</td><td>Working hours</td></tr></table>

Edition 4.0                                                                                                                                                                                    **50** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

# Appendix E · Identifier index

Every record identifier used in this manual, against the sections it appears in. Search the ticketing system for the live record; this index tells you where the context is written down.

<table><tr><th>IDENTIFIER</th><th>WHAT IT IS</th><th>SECTIONS</th></tr><tr><td>KB0001</td><td>VPN authentication fails after a password change</td><td>5.2, 6.1, 6.4, 7.2, 10.2, 11.5</td></tr><tr><td>KB0002</td><td>Outlook shows Disconnected and no mail is delivered</td><td>3.2, 5.2, 6.1, 6.5</td></tr><tr><td>KB0003</td><td>Mapped shared drive is missing after sign-in</td><td>5.2, 6.1, 6.6, 8.3</td></tr><tr><td>KB0004</td><td>Print jobs queue but nothing prints</td><td>5.2, 6.1, 6.7, 7.3, 10.2</td></tr><tr><td>KB0005</td><td>Account is locked after repeated failed sign-ins</td><td>5.2, 6.1, 6.3, 6.8, 7.4, 8.3, 10.2</td></tr><tr><td>KB0006</td><td>Multi-factor authentication after a lost or replaced device</td><td>6.1, 6.9</td></tr><tr><td>KB0007</td><td>Laptop performance degrades after a system update</td><td>5.2, 6.1, 6.10</td></tr><tr><td>KB0008</td><td>SAP GUI connection times out</td><td>5.2, 6.1, 6.11, 8.3</td></tr><tr><td>KB0009</td><td>Wi-Fi drops on the 5 GHz corporate network</td><td>5.2, 6.1, 6.12, 8.3, 10.2</td></tr><tr><td>KB0010 v1</td><td>Order service pool exhaustion — retired, do not apply</td><td>6.1, 6.13, 9.2, 9.5, 9.6</td></tr><tr><td>KB0010 v2</td><td>Order service pool exhaustion — current</td><td>5.2, 6.1, 6.13, 7.5, 10.2</td></tr><tr><td>INC0009884</td><td>The 14 March order-processing incident</td><td>9.1, 9.3</td></tr><tr><td>INC0010023</td><td>VPN authentication failure, 08 Sep 2026</td><td>7.1, 7.2, 11.5</td></tr><tr><td>INC0010047</td><td>Meeting room 4 printer, mechanical fault</td><td>7.3, 11.4</td></tr><tr><td>INC0010052</td><td>Order service pool saturation, 08 Sep 2026</td><td>7.5, 10.4</td></tr><tr><td>INC0010064</td><td>Three symptoms, one lockout</td><td>7.4</td></tr><tr><td>PRB0040012</td><td>Repeat lockouts from cached mobile credentials</td><td>7.4, 8.2, 8.3, 10.2</td></tr><tr><td>PRB0040018</td><td>Connection lifetime defect on order-processing</td><td>7.5, 8.2, 9.6</td></tr><tr><td>PRB0040021</td><td>5 GHz roaming drops, floors 3 and 4</td><td>5.2, 8.2, 10.2</td></tr><tr><td>PRB0040026</td><td>Post-update endpoint degradation on one model</td><td>8.2</td></tr><tr><td>PRB0040029</td><td>Third-floor print queue stalls</td><td>8.2, 8.3</td></tr><tr><td>KE0000034</td><td>SAP connection profile names a decommissioned server</td><td>5.2, 8.3</td></tr><tr><td>KE0000041</td><td>Drive mapping absent although the path is reachable</td><td>8.3</td></tr><tr><td>KE0000047</td><td>Account re-locks within minutes of an unlock</td><td>8.3</td></tr><tr><td>KE0000052</td><td>Wireless drops while roaming</td><td>8.3</td></tr><tr><td>KE0000055</td><td>Print queue stalls after a spooler restart</td><td>8.3</td></tr><tr><td>CHG0030401</td><td>Spooler restart and queue clear — standard</td><td>10.2</td></tr></table>

Edition 4.0

**51** of 52  ·

## BARQ Systems · IT Service Operations Manual

## INTERNAL DOCUMENT

<table><tr><th>IDENTIFIER</th><th>WHAT IT IS</th><th>SECTIONS</th></tr><tr><td>CHG0030418</td><td>Cached credential clear — standard</td><td>10.2</td></tr><tr><td>CHG0030422</td><td>Account unlock after verification — standard</td><td>10.2</td></tr><tr><td>CHG0030455</td><td>Order service pool drain — standard procedure, emergency authorisation</td><td>6.13, 7.5, 9.3, 9.6, 10.2, 10.4</td></tr><tr><td>CHG0030470</td><td>Wireless driver update on one endpoint — standard</td><td>10.2</td></tr><tr><td>CHG0030588</td><td>Wireless controller firmware</td><td>8.2, 8.3</td></tr><tr><td>CHG0030602</td><td>Login script rewrite</td><td>8.3</td></tr><tr><td>RITM0010877</td><td>MFA reset request</td><td>1.2, 6.9</td></tr><tr><td>MIR-2026-03</td><td>Order processing unavailable, 14 March 2026</td><td>6.13, 7.5, 9, 10.2</td></tr></table>

End of manual. Edition 4.0, published 11 August 2026. Next scheduled review 10 August 2027.

Edition 4.0

**52** of 52 ·