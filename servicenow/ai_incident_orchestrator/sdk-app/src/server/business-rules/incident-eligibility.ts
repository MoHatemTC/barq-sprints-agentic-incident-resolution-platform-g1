import { gs } from '@servicenow/glide'

const RELEVANT_FIELDS = [
    'active',
    'category',
    'x_2215032_ai_inc_0_ai_enabled',
    'x_2215032_ai_inc_0_ai_processing_state',
    'x_2215032_ai_inc_0_ai_human_lock',
]

const SUPPORTED_CATEGORIES = ['software', 'hardware', 'network', 'database']

export type IncidentEligibilityEvent = {
    event_id: string
    sys_id: string
    number: string
    event_type: 'incident.created' | 'incident.updated'
}

function hasRelevantUpdate(current: any, previous: any): boolean {
    return RELEVANT_FIELDS.some(function fieldChanged(fieldName) {
        return current.getValue(fieldName) !== previous.getValue(fieldName)
    })
}

function suppress(reason: string): void {
    gs.info('S1.3 eligibility suppressed: ' + reason)
}

export function evaluateIncidentEligibility(current: any, previous: any): IncidentEligibilityEvent | null {
    const operation = current.operation()

    if (operation === 'update') {
        if (!previous) {
            suppress('previous_unavailable')
            return null
        }

        if (!hasRelevantUpdate(current, previous)) {
            return null
        }
    }

    const processingState = current.getValue('x_2215032_ai_inc_0_ai_processing_state')
    const category = current.getValue('category')
    const humanLock = current.getValue('x_2215032_ai_inc_0_ai_human_lock')

    if (current.getValue('active') !== '1') {
        suppress('inactive')
        return null
    }

    if (current.getValue('x_2215032_ai_inc_0_ai_enabled') !== '1') {
        suppress('ai_disabled')
        return null
    }

    if (processingState === 'complete') {
        suppress('already_processed')
        return null
    }

    if (SUPPORTED_CATEGORIES.indexOf(category) === -1) {
        suppress('unsupported_category')
        return null
    }

    if (processingState === 'in_progress') {
        suppress('already_running')
        return null
    }

    if (processingState === 'awaiting_approval') {
        suppress('awaiting_approval')
        return null
    }

    if (processingState !== 'pending' && processingState !== 'failed') {
        suppress('invalid_processing_state')
        return null
    }

    if (humanLock === '1') {
        suppress('human_locked')
        return null
    }

    if (humanLock !== '0') {
        suppress('invalid_human_lock')
        return null
    }

    const eventType = operation === 'insert' ? 'incident.created' : 'incident.updated'
    const event: IncidentEligibilityEvent = {
        event_id: gs.generateGUID(),
        sys_id: current.getUniqueValue(),
        number: current.getValue('number'),
        event_type: eventType,
    }

    gs.info('S1.3 eligibility passed: ' + eventType)
    return event
}
