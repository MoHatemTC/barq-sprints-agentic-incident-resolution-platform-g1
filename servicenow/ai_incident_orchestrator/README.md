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

### Advisories published 2026-09-18 (audit step failing since 2026-10-03)

Three advisories published on 2026-09-18 made the audit step report **19 vulnerabilities, 16 high**. None of the vulnerable leaves has a patched release, so the leaves cannot be overridden; instead the packages that pull them in are moved to releases that no longer do:

| Package | Was | Overridden to | Why |
|---|---|---|---|
| `node-gyp` | 11.5.0 | `^13.1.0` | 13.x no longer depends on `make-fetch-happen`, which removes `http-cache-semantics` (GHSA-ch52-4w7c-c8xp, `<=4.2.0`, no fix). Reached through `libxmljs2`. Needs Node `^22.22.2`, which CI's Node 22 satisfies. |
| `livereload` | 0.9.3 | `^0.10.3` | 0.10.x uses `chokidar` 4, which no longer depends on `braces`. Reached through `rollup-plugin-livereload` in `@servicenow/isomorphic-rollup`. |
| `fflate` | 0.8.2 | `^0.8.3` | GHSA-px8p-9vwx-vf98 (moderate), fixed in 0.8.3 — the same version SDK 4.13.3 pins. |

Result: **19 → 9 vulnerabilities, 9 high, 0 moderate.** Same verification as above on Node 22.23.3: `npm ci`, `npx now-sdk build --frozenKeys` exits 0, the build leaves `git status` clean (`keys.ts` unchanged), 28/28 SDK tests pass, and the built records still match the exported update set.

**Still open: `braces` (GHSA-vfj7-8cjw-p6xm, CVE-2026-93687, `<=3.0.3`).** The nine remaining highs are one chain: `@servicenow/sdk-build-core` pins `fast-glob` 3.3.3 → `micromatch` → `braces`. Every published `fast-glob` and `micromatch` depends on `braces`, and 3.0.3 is the latest `braces`; the upstream fix (micromatch/braces#72) is not merged or released. Upgrading the SDK does not help — 4.13.3 pins the same `fast-glob` 3.3.3. When a fixed `braces` is published, add it to `overrides`, delete its entry from `sdk-app/audit-exceptions.json` and re-run the gate above.

Until then the advisory is accepted, and only that one. The audit step runs `sdk-app/audit-gate.cjs`, which applies the same bar as `npm audit --audit-level=high` but skips advisories listed in `sdk-app/audit-exceptions.json`. Each entry gives one GHSA id, the reason it cannot reach anything we ship, and an expiry date. Any other high or critical advisory still fails the build, an expired entry fails it again, and an entry that is no longer needed is reported. `braces` qualifies because it only expands glob patterns written in this repository, at build time, and nothing from it reaches the instance. Its entry expires on 2026-10-31.
