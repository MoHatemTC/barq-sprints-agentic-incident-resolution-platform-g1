# Security policy

This project writes into a live ITSM platform. Three constraints are non-negotiable
and come straight from the product requirements.

## No admin credentials

FR-06 requires all ServiceNow API access to authenticate via OAuth as a **dedicated
least-privilege integration user**. Admin-credential authentication is one of the
PRD's three explicit exclusions, alongside polling and Kubernetes.

No admin credential may exist in configuration or code — including as a placeholder
in `.env.example`, which is committed and is the first place anyone looks.

## No secrets in logs or traces

NFR-06 requires that no secret appears in any trace or log. Be careful with failure
paths: an OAuth token endpoint can echo grant parameters back in its error body, so
log the status code and a bounded excerpt rather than the whole response.

## Least privilege is verified, not asserted

The permission matrix must record **observed** results from executed attempts,
including attempts that were correctly denied. An intended matrix is a design
document; a verified one is a security control, and only the second is worth showing
a risk owner.

A verification harness must fail closed. If it cannot reach the resource under test,
it must report failure — never pass because a request returned nothing.

## The human lock

`x_2215032_ai_inc_0_ai_human_lock` on the Incident table is the hard stop for
automated processing. The integration identity may read it and must never write or
clear it. Check it at eligibility time **and** immediately before any write, since
work may have been queued before the lock was set.

## What automated scanning does and does not cover

CodeQL scans the Python under `src/`, `scripts/` and `tests/`, and the hand-written
ServiceNow TypeScript under `servicenow/`. Generated Fluent/SDK output is excluded as
noise.

**It cannot scan scripts embedded inside exported update-set XML.** ACL condition
scripts, Business Rule bodies and Client Scripts ship inside `<script>` elements in the
`ai_incident_orchestrator_s1_*.xml` files, and no static analyser reads them there. Those
are reviewed by a human — that is what the `CODEOWNERS` entry on each update set is for.
Treat a change to an ACL or a Business Rule in an update set as unscanned by definition.

## Reporting

Open an issue using the **Security concern** template. If a real credential has been
committed, treat it as exposed: rotate it first, then remove it from history.
