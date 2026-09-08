# AI Incident Orchestrator — ServiceNow Package

This directory holds the ServiceNow artifact for S1.1.

Final exported file:

```text
ai_incident_orchestrator_s1_1.xml
```

The XML was exported through the official ServiceNow SDK from completed update set `4fc6308073530b502aedfed25ab8b7ab`, which contains the complete **AI Incident Orchestrator** scoped application. It has 39 update records and zero delete actions and was not edited after export.

Before committing the XML:

1. Review the update-set contents for every field, choice, form/view record, validation rule, and application record.
2. Confirm no S1.1 record was captured under Global scope.
3. Preview and commit the XML on a clean secondary PDI using the same ServiceNow family release.
4. Verify the Incident field model, form section, and confidence validation after import.
5. Record the result in `docs/sprint1_field_model.md` and capture screenshots under `docs/screenshots/s1-1/`.

The official SDK source under `sdk-app/` is the reproducible implementation source. It is not a substitute for the mentor-required exported update-set XML.
