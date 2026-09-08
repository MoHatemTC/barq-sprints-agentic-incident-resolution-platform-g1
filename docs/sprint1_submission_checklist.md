# S1.1 Submission Checklist

Do not request a formal review until every required item is checked.

## Scope and schema

- [ ] AI Incident Orchestrator exists as a dedicated scoped application.
- [ ] Exact scope name is recorded in `docs/sprint1_field_model.md`.
- [ ] No S1.1 artifact resides in Global scope or the Global/default update set.
- [ ] Processing State includes all five required internal values.
- [ ] Classification type and vocabulary are mentor-approved.
- [ ] Confidence is Decimal and server-side bounded to 0–1 inclusive.
- [ ] Suggestion and Resolution are separate fields.
- [ ] Model Name and Agent Version are present.
- [ ] Processing Start and Processing End are Date/Time fields.
- [ ] Human Review semantics are confirmed and implemented.
- [ ] Human Lock is dedicated and documented as a hard automation stop.
- [ ] Failure Reason captures explicit failed-run information.
- [ ] AI Enabled decision is documented and implemented if approved.

## Form and behavior

- [ ] The approved Incident view contains a readable AI Incident Orchestrator section.
- [ ] All required fields are present exactly once.
- [ ] Confidence accepts 0, 0.50, and 1.
- [ ] Confidence rejects -0.01 and 1.01 server-side.
- [ ] Suggestion and Resolution retain different values.
- [ ] Start/end timestamps persist and are queryable.
- [ ] Failed state and Failure Reason persist.
- [ ] Human-lock enforcement ownership is recorded and integrated/tested when available.

## Documentation and evidence

- [ ] Field dictionary includes exact technical names, types, lengths, values, defaults, writers, and permissions.
- [ ] Suggestion/resolution rationale is included.
- [ ] Scope, validation, lifecycle, and permission assumptions are documented.
- [ ] Required Incident form screenshots are readable.
- [ ] Supporting Studio, choice, validation, and update-set screenshots are included.
- [ ] Screenshots contain no credentials or secrets.
- [ ] No `TBD`, `REPLACE_WITH`, or placeholder text remains.

## Export and import verification

- [ ] Final application/update set contents were reviewed.
- [ ] Final update set is Complete.
- [ ] XML was exported by ServiceNow and never hand-edited.
- [ ] XML is stored in `servicenow/ai_incident_orchestrator/`.
- [ ] XML was previewed on a clean secondary PDI of the same release.
- [ ] Preview produced no unresolved errors or missing dependencies.
- [ ] Commit completed without manual configuration/repair.
- [ ] Application, fields, form, choices, and validation were retested after import.
- [ ] Import evidence is recorded in the dictionary/screenshots.

## GitHub and submission

- [ ] Work is on `feature/s1-1-ai-incident-field-model`, not directly on `main`.
- [ ] `git diff --check` passes.
- [ ] The feature branch is pushed.
- [ ] A pull request into `main` is open and accessible.
- [ ] Every repository link was opened and verified.
- [ ] Airtable status is updated when access becomes available.
- [ ] Sarah receives the PR/repository, XML, dictionary, and screenshot links.
- [ ] Formal review is requested only after all checks pass.

## Submission message

```text
Hi Eng. Sarah,

I have completed S1.1: Scoped AI Incident Orchestrator Application & Incident Field Model.

The submission includes:
- Scoped AI Incident Orchestrator application and complete Incident AI field model
- Server-side confidence validation and organized Incident form section
- Exported update-set XML
- docs/sprint1_field_model.md
- Incident form and import-verification screenshots

Pull request: [LINK]
Update-set XML: [LINK]
Field dictionary: [LINK]
Screenshots: [LINK]

I verified that every link is accessible and that the update set previews, commits, and works on a clean secondary instance without manual repair.

Please let me know if you need any clarification. Thank you.
```

