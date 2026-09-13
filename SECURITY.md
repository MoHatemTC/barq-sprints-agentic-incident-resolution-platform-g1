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

## Reporting

**Route depends on what you found.**

### A vulnerability, or a credential that is actually exposed

Use **private reporting**: the repository's Security tab → *Report a vulnerability*. If
that is unavailable, message a repository admin (@MoHatemTC) directly. Do **not** open a
public issue.

This repository is public. A public issue asking for proof means publishing a working
exploit, a leaked token, or executed permission attempts against the team's ServiceNow
instance — which creates the exposure it is reporting.

If a real credential has been committed or posted anywhere, treat it as exposed:
**rotate it first**, then remove it from history, then report.

### A hardening proposal

Open an issue using the **Security concern** template — for example "this ACL should be
narrower", "this role grant is broader than the contract requires", "this check fails
open". These are design discussions and belong in the open.

**Never paste credentials, tokens, session cookies or exploit details into a public
issue**, whichever template you use.
