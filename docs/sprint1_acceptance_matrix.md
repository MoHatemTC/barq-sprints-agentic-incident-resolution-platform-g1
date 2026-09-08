# S1.1 Acceptance Evidence Matrix

This matrix maps Ahmed Mansour's S1.1 brief to concrete implementation and evidence. It is intended to make formal review deterministic.

| Acceptance requirement | Implementation | Evidence |
|---|---|---|
| Dedicated scoped application; zero Global artifacts | AI Incident Orchestrator, scope `x_2215032_ai_inc_0`, application sys_id `51a63bbf738bc7502aedfed25ab8b789` | `01-studio-application-scope.png`; `docs/sprint1_source_pdi_verification.md`; exported XML application record |
| Processing State with five required states | Choice field with `pending`, `in_progress`, `awaiting_approval`, `complete`, and `failed` | `03-processing-state-choices.png`; `src/fluent/incident-fields.now.ts` |
| Classification | String (100), intentionally taxonomy-flexible until S1.4 | Field dictionary; populated form screenshot |
| Confidence decimal bounded inclusively from 0 to 1 | Decimal scale 2 with scoped onChange and onSubmit validation; downstream API-writer invariant documented | `05-confidence-validation.png`; `09-secondary-import-verification.png`; `src/fluent/confidence-validation.now.ts`; boundary tests for `0` and `1` passed |
| Independent Suggestion and Resolution | Separate String (4000) columns | `02-incident-ai-section.png`; field dictionary rationale; populated values remain distinct |
| Model and code attribution | Model Name String (100) and Agent Version String (64) | `02-incident-ai-section.png`; field dictionary |
| Queryable start/end timing | Two Date/Time columns | `02-incident-ai-section.png`; field dictionary |
| Human Review and Human Lock | Separate Boolean fields; Human Review means review required, while Human Lock is the downstream hard-stop contract | `02-incident-ai-section.png`; field dictionary; S1.3 ownership explicitly documented |
| Explicit failure reason | Failure Reason String (4000) | Form screenshots; field dictionary |
| Organized Default-view Incident form section | Dedicated AI Incident Orchestrator section with all 13 fields | `02-incident-ai-section.png`; `04-populated-ai-fields.png`; `src/fluent/incident-form-layout.now.ts` |
| Exported ServiceNow update-set XML | Completed update set exported through the official ServiceNow SDK | `servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml`; 39 records; zero delete actions |
| Markdown field dictionary | Types, values, writers, intended permissions, lifecycle, scope, and rationale documented | `docs/sprint1_field_model.md` |
| Incident-form screenshots | Scope, layout, values, choices, and validation captured | `docs/screenshots/s1-1/` |
| Clean import without manual repair | Preview and commit on authorized teammate PDI `dev204871` | `docs/sprint1_secondary_import_verification.md`; `07-secondary-import-preview.png`: 39 inserts and zero collisions; `08-secondary-import-committed.png`; `09-secondary-import-verification.png` |
| Reproducible implementation | Official ServiceNow SDK project and implementation runbook | `servicenow/ai_incident_orchestrator/sdk-app/`; `docs/sprint1_implementation_runbook.md`; `npm run build` passes |

## Scope boundary

This submission is strictly S1.1. OAuth roles and field ACL enforcement belong to Mohamed's S1.2 workstream, while eligibility and Human Lock runtime enforcement belong to S1.3. AI Enabled is included now because Sarah confirmed S1.3 depends on it.

Unlike Task 0, this S1.1 brief does not request a demo video or reflection document. Its required package is the update-set XML, field dictionary, and Incident-form screenshots; all three are included.
