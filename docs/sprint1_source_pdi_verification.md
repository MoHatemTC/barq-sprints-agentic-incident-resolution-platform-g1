# S1.1 Source-PDI Verification

Verification performed on September 8, 2026 against source PDI `dev434590` on the Australia release.

## Verified application metadata

- Application: AI Incident Orchestrator
- Scope: `x_2215032_ai_inc_0`
- Application sys_id: `51a63bbf738bc7502aedfed25ab8b789`
- Final published update set: AI Incident Orchestrator
- Final update set sys_id: `a76c850473170b502aedfed25ab8b7bc`
- Final state: Complete
- Exported package: `servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml`
- Package inspection: 39 update records and zero `DELETE` actions

## Verified source records

- Thirteen Incident dictionary fields use the generated `x_2215032_ai_inc_0_` prefix and reference the application in both `sys_scope` and `sys_package`.
- AI Processing State has five choices: `pending`, `in_progress`, `awaiting_approval`, `complete`, and `failed`.
- The Default Incident view contains the AI Incident Orchestrator section with thirteen fields plus the layout split markers.
- Two scoped Client Scripts validate AI Confidence on change and on submit.
- Synthetic Incident `INC0010018` stores distinct suggestion/resolution values, model attribution, timing, review/lock flags, and failure information.

## Secondary-instance verification

The exported XML was previewed and committed on authorized teammate PDI `dev204871`. Preview reported 39 inserts, zero updates, zero deletes, and zero collisions. Commit completed without manual repair. An existing Incident then displayed the imported AI Incident Orchestrator section, fields, processing-state default, and confidence validation; an out-of-range value was automatically removed with the expected error.
