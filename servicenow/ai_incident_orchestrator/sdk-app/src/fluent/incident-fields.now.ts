import {
    BooleanColumn,
    ChoiceColumn,
    DateTimeColumn,
    DecimalColumn,
    IntegerColumn,
    StringColumn,
    Table,
} from '@servicenow/sdk/core'

/**
 * S1.1 scoped columns added to the global Incident table.
 *
 * Every column name carries the generated application prefix as required for
 * cross-scope augmentation of the global Incident table.
 */
export const incident = Table({
    augments: 'incident',
    schema: {
        x_2215032_ai_inc_0_ai_enabled: BooleanColumn({
            label: 'AI Enabled',
            default: false,
            active: true,
            hint: 'Explicit opt-in flag checked before automated AI processing begins.',
        }),
        x_2215032_ai_inc_0_ai_processing_state: ChoiceColumn({
            label: 'AI Processing State',
            default: 'pending',
            dropdown: 'dropdown_without_none',
            choices: {
                pending: { label: 'Pending', sequence: 100 },
                in_progress: { label: 'In Progress', sequence: 200 },
                awaiting_approval: { label: 'Awaiting Approval', sequence: 300 },
                complete: { label: 'Complete', sequence: 400 },
                failed: { label: 'Failed', sequence: 500 },
            },
            active: true,
            hint: 'Durable lifecycle state for AI processing.',
        }),
        x_2215032_ai_inc_0_ai_classification: StringColumn({
            label: 'AI Classification',
            maxLength: 100,
            active: true,
            hint: 'Incident class produced by the agent classifier.',
        }),
        x_2215032_ai_inc_0_ai_confidence: DecimalColumn({
            label: 'AI Confidence',
            scale: 2,
            active: true,
            hint: 'Normalized confidence score from 0.00 through 1.00.',
        }),
        x_2215032_ai_inc_0_ai_suggestion: StringColumn({
            label: 'AI Suggestion',
            maxLength: 4000,
            active: true,
            hint: 'Original AI-proposed response or action before approval or execution.',
        }),
        x_2215032_ai_inc_0_ai_resolution: StringColumn({
            label: 'AI Resolution',
            maxLength: 4000,
            active: true,
            hint: 'Final accepted and applied resolution; independent from the suggestion.',
        }),
        x_2215032_ai_inc_0_ai_model_name: StringColumn({
            label: 'AI Model Name',
            maxLength: 100,
            active: true,
            hint: 'Exact provider/model identifier used for the run.',
        }),
        x_2215032_ai_inc_0_ai_agent_version: StringColumn({
            label: 'AI Agent Version',
            maxLength: 64,
            active: true,
            hint: 'Exact agent release or code version used for the run.',
        }),
        x_2215032_ai_inc_0_ai_processing_start: DateTimeColumn({
            label: 'AI Processing Start',
            active: true,
            hint: 'Timestamp at which AI processing began.',
        }),
        x_2215032_ai_inc_0_ai_processing_end: DateTimeColumn({
            label: 'AI Processing End',
            active: true,
            hint: 'Timestamp at which AI processing completed or failed.',
        }),
        x_2215032_ai_inc_0_ai_human_review_required: BooleanColumn({
            label: 'AI Human Review Required',
            default: false,
            active: true,
            hint: 'Indicates that a human must inspect or approve the proposed action.',
        }),
        x_2215032_ai_inc_0_ai_human_lock: BooleanColumn({
            label: 'AI Human Lock',
            default: false,
            active: true,
            hint: 'Hard human-controlled override that stops automated AI processing.',
        }),
        x_2215032_ai_inc_0_ai_retry_count: IntegerColumn({
            label: 'AI Retry Count',
            default: 0,
            active: true,
            hint: 'Number of retries already attempted after the original processing attempt.',
        }),
        x_2215032_ai_inc_0_ai_failure_reason: StringColumn({
            label: 'AI Failure Reason',
            maxLength: 4000,
            active: true,
            hint: 'Explicit diagnostic reason for a failed processing attempt.',
        }),
    },
})
