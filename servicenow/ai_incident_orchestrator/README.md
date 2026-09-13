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
