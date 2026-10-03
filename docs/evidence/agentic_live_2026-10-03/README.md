# Live regression, 2026-10-03 (shared dev407364 + shared EC2 at `569c4a9`)

One fresh full run after the afternoon fixes; fixtures are labelled `[BARQ-TEST-2026-10-03]`.

| Suite | Result | Incidents |
|---|---|---|
| Core (`agentic_core.json`) | 7/7 | INC0010346–INC0010352 |
| Triage (`triage.json`) | 4/4 | INC0010353–INC0010359 |
| Permissions (`permissions.json`) | 12/12 | — |
| Conversation and page buttons (`conversation.json`) | 5/5 | INC0010361–INC0010365 |
| Users' page (`user_chat.json`) | 10/10 | INC0010366 |
| Engineer page per role and state (`engineer_page.json`) | 8/8 | — |

Screens (`screens/`): the engineer page with a fix waiting for an engineer, the approver's page
for a paused fix, and the users' chat and ticket view, taken as the real users.

Found live and fixed before this run: the semantic cache reusing a fix that came from another
ticket's conversation; a stale paused run after a hand-back; buttons shown on every incident
(254-character condition limit); approvers unable to approve a fix paused before any draft;
raw markdown and internal references on the users' page.
