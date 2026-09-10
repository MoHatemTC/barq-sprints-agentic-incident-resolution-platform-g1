import { BusinessRule } from '@servicenow/sdk/core'
import { evaluateIncidentEligibility } from '../server/business-rules/incident-eligibility'

/**
 * S1.3 eligibility boundary for Incident automation.
 *
 * This rule deliberately performs no writes and no outbound work. The final
 * eligible branch is the extension point for constructing the approved event
 * contract in the next phase.
 */
export const incidentEligibilityBusinessRule = BusinessRule({
    $id: Now.ID['incident_eligibility_business_rule'],
    name: 'AI Incident Orchestrator - Evaluate Eligibility',
    table: 'incident',
    when: 'after',
    action: ['insert', 'update'],
    order: 100,
    active: true,
    description: 'Evaluates whether an inserted or eligibility-relevant updated Incident may proceed to event preparation.',
    script: evaluateIncidentEligibility,
})
