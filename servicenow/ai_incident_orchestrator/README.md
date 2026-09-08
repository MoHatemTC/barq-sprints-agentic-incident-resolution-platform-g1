# AI Incident Orchestrator — ServiceNow Package

This directory holds the ServiceNow artifact for S1.1.

Expected final file:

```text
ai_incident_orchestrator_s1_1.xml
```

The XML must be exported directly from a completed ServiceNow update set containing the complete **AI Incident Orchestrator** scoped application. It must not be handwritten or edited after export.

Before committing the XML:

1. Review the update-set contents for every field, choice, form/view record, validation rule, and application record.
2. Confirm no S1.1 record was captured under Global scope.
3. Preview and commit the XML on a clean secondary PDI using the same ServiceNow family release.
4. Verify the Incident field model, form section, and confidence validation after import.
5. Record the result in `docs/sprint1_field_model.md` and capture screenshots under `docs/screenshots/s1-1/`.

Reference-only implementation logic lives under `reference/`. It is not a substitute for the exported update-set XML.

