import { BusinessRule } from '@servicenow/sdk/core'
import { escalateExhaustedRetry } from '../server/business-rules/retry-escalation'

export const retryEscalationBusinessRule = BusinessRule({
    $id: Now.ID['retry_escalation_business_rule'],
    name: 'AI Incident Orchestrator - Retry Escalation',
    table: 'incident',
    when: 'before',
    action: ['update'],
    order: 90,
    active: true,
    description: 'Advances an eligible new failed transition or requires human review when retries are invalid or exhausted.',
    script: escalateExhaustedRetry,
})
