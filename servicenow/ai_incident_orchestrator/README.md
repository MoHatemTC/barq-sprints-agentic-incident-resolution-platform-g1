# AI Incident Orchestrator — ServiceNow Package

This directory holds the ServiceNow artifact for S1.1.

Final exported file:

```text
ai_incident_orchestrator_s1_1.xml
```

The XML was exported with ServiceNow's **Export to XML** (the schema was deployed via the official ServiceNow SDK) from completed update set `a76c850473170b502aedfed25ab8b7bc`, which contains the complete **AI Incident Orchestrator** scoped application. It has 39 update records and zero delete actions and was not edited after export.

Before committing the XML:

1. Review the update-set contents for every field, choice, form/view record, validation rule, and application record.
2. Confirm no S1.1 record was captured under Global scope.
3. Preview and commit the XML on a clean secondary PDI using the same ServiceNow family release.
4. Verify the Incident field model, form section, and confidence validation after import.
5. Record the result in `docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md` and capture screenshots under `docs/sprint-1/s1.1-scoped-app-and-field-model/screenshots/`.

## Which artifact is authoritative

`ai_incident_orchestrator_s1_1.xml` is. It is what was previewed and committed on `dev434590` and `dev204871`, and its record sys_ids are the ones live on those instances.

The official SDK source under `sdk-app/` is the reproducible implementation source, and it is not a substitute for the mentor-required exported update-set XML. "Reproducible" is a claim with a verified version attached to it:

| | |
|---|---|
| Verified SDK version | **`@servicenow/sdk` 4.8.0** (with `@servicenow/glide` 27.0.5) |
| Verified with | `npm ci && npx now-sdk build --frozenKeys` |
| Result | exit 0; `src/fluent/generated/keys.ts` byte-identical; the five processing-state choice sys_ids equal to those in the exported XML |
| Enforced by | `.github/workflows/servicenow-sdk.yml` on every PR touching `servicenow/**` |

**Do not bump `@servicenow/sdk` without re-verifying those rows.** 4.11.2 was merged in #24 on a Python-only CI run and does not satisfy them: the build rewrites the committed `keys.ts`, marks all five exported choice sys_ids `deleted: true`, mints replacements that differ on every fresh build, and moves the choice file to `dist/app/author_elective_update/`. Following the runbook with that build would replace the Processing State choices on the target instance. See #54; the pin back to 4.8.0 is deliberate.

## Build-toolchain advisories (#73)

Pinning to 4.8.0 for reproducibility costs advisory coverage: `npm audit` reported **19 vulnerabilities, 4 of them high** at the pin, against 14/1 at 4.11.2. npm's only offered remedy was `@servicenow/sdk` 4.12.1, which #54 rules out.

The four highs are instead resolved with an `overrides` block in `package.json`, which is safe here precisely because the build output is verified:

| Package | Was | Overridden to | Advisory range |
|---|---|---|---|
| `@fastify/static` | 8.3.0 | `^10.1.3` | `<=10.1.1` |
| `js-yaml` | 3.14.1 | `^4.3.2` | `<=3.15.1` |
| `tmp` | 0.0.33 | `^0.2.7` | `<=0.2.5` |
| `undici` | 6.25.0 | `^7.29.1` | `<=6.27.0` |

Result: **19 → 10 vulnerabilities, 0 high, 0 critical.** Verified that this changes nothing the instance receives — `npx now-sdk build --frozenKeys` still exits 0, `keys.ts` is byte-identical to the committed file, and all 5 choice records and 13 field definitions still match the exported update set.

### Dependabot alerts closed on 2026-09-26

Repository-level Dependabot security alerts are on, and they found four more in the same
transitive toolchain (`@servicenow/isomorphic-rollup` pulls both). They were not covered by
the table above because they are moderate/low rather than high:

| Package | Was | Overridden to | Advisory |
|---|---|---|---|
| `fastify` | 5.8.5 | `^5.12.1` (resolved 5.12.5) | GHSA-3m5p-2c4r-xxw2 — `X-Forwarded-*` spoofing under `trustProxy` hop-count |
| `fastify` | 5.8.5 | `^5.12.1` (resolved 5.12.5) | GHSA-w2qp-rph6-63g4 — schema validation bypass via root primitive coercion |
| `joi` | 17.13.3 | `^17.13.6` (resolved 17.13.8) | GHSA-gg4h-3hg2-grpc — `object().rename()` template target prototype pollution |
| `joi` | 17.13.3 | `^17.13.6` (resolved 17.13.8) | GHSA-6w3j-5fw6-r9vr — `__proto__` language key prototype pollution |

Both fixed lines carry **0 known advisories** in OSV for the resolved versions.

Result after these two overrides: **19 → 7 vulnerabilities, 0 high, 0 critical.** Same
verification as above: `npx now-sdk build --frozenKeys` exits 0, `keys.ts` is
byte-identical to the committed file, the build adds nothing to `git status`, and the
built records still match the exported update set.

The remaining **7 moderates are accepted**. All are transitive dependencies of the SDK's CLI tooling, they run only at build time on developer machines and CI, and nothing from them is shipped to a ServiceNow instance — the deliverable is the exported XML. Overriding them further would mean major bumps deeper inside a vendor CLI for no change to what we ship. The real fix is a future SDK release that reproduces the export; that is Sprint 2 work, gated by `.github/workflows/servicenow-sdk.yml`.

`npm audit --package-lock-only --audit-level=high` runs in that workflow, so a new high fails the build rather than going unnoticed. The four alerts Dependabot raised are fixed above; any new alert on this lockfile needs the same treatment (find the fixed line in OSV, override, re-run the build gate).
