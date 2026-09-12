import { BusinessRule, Property } from '@servicenow/sdk/core'
import { evaluateIncidentEligibility } from '../server/business-rules/incident-eligibility'

export const s13SupportedCategories = Property({
    $id: Now.ID['s1_3_supported_categories'],
    name: 'x_2215032_ai_inc_0.s1_3_supported_categories',
    type: 'string',
    value: 'software,network,hardware,inquiry',
    description: 'Comma-separated Incident categories eligible for S1.3 processing.',
})

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
