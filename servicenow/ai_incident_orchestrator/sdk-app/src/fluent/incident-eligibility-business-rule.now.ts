import { BusinessRule } from '@servicenow/sdk/core'
import { evaluateIncidentEligibility } from '../server/business-rules/incident-eligibility'

/**
 * S1.3 eligibility boundary for Incident automation.
 *
 * This rule deliberately performs no writes or HTTP. Eligible records enqueue
 * the registered S1.3 event for asynchronous delivery by a Script Action.
 */
export const incidentEligibilityBusinessRule = BusinessRule({
    $id: Now.ID['incident_eligibility_business_rule'],
    name: 'AI Incident Orchestrator - Evaluate Eligibility',
    table: 'incident',
    when: 'after',
    action: ['insert', 'update'],
    order: 100,
    active: true,
    description: 'Evaluates Incident eligibility and queues the minimal S1.3 outbound event for asynchronous delivery.',
    script: evaluateIncidentEligibility,
})
