import { gs } from '@servicenow/glide'

const RELEVANT_FIELDS = [
    'active',
    'category',
    'x_2215032_ai_inc_0_ai_enabled',
    'x_2215032_ai_inc_0_ai_processing_state',
    'x_2215032_ai_inc_0_ai_human_lock',
    'x_2215032_ai_inc_0_ai_retry_count',
]

const SUPPORTED_CATEGORIES = ['software', 'hardware', 'network', 'database']

function hasRelevantUpdate(current: any, previous: any): boolean {
    return RELEVANT_FIELDS.some(function fieldChanged(fieldName) {
        return current.getValue(fieldName) !== previous.getValue(fieldName)
    })
}

function suppress(reason: string): void {
    gs.info('S1.3 eligibility suppressed: ' + reason)
}

export function evaluateIncidentEligibility(current: any, previous: any): void {
    const operation = current.operation()

    if (operation === 'update') {
        if (!previous) {
            suppress('previous_unavailable')
            return
        }

        if (!hasRelevantUpdate(current, previous)) {
            return
        }
    }

    const processingState = current.getValue('x_2215032_ai_inc_0_ai_processing_state')
    const category = current.getValue('category')
    const humanLock = current.getValue('x_2215032_ai_inc_0_ai_human_lock')
    const retryValue = current.getValue('x_2215032_ai_inc_0_ai_retry_count')
    const currentRetryCount = parseInt(retryValue || '0', 10)

    if (current.getValue('active') !== '1') {
        suppress('inactive')
        return
    }

    if (current.getValue('x_2215032_ai_inc_0_ai_enabled') !== '1') {
        suppress('ai_disabled')
        return
    }

    if (processingState === 'complete') {
        suppress('already_processed')
        return
    }

    if (SUPPORTED_CATEGORIES.indexOf(category) === -1) {
        suppress('unsupported_category')
        return
    }

    if (processingState === 'in_progress') {
        suppress('already_running')
        return
    }

    if (processingState === 'awaiting_approval') {
        suppress('awaiting_approval')
        return
    }

    if (processingState !== 'pending' && processingState !== 'failed') {
        suppress('invalid_processing_state')
        return
    }

    if (isNaN(currentRetryCount) || currentRetryCount < 0) {
        suppress('invalid_retry_count')
        return
    }

    let retryCount = currentRetryCount

    if (processingState === 'failed' && operation === 'update') {
        if (previous.getValue('x_2215032_ai_inc_0_ai_processing_state') === 'failed') {
            return
        }

        const previousRetryValue = previous.getValue('x_2215032_ai_inc_0_ai_retry_count')
        retryCount = parseInt(previousRetryValue || '0', 10)

        if (isNaN(retryCount) || retryCount < 0) {
            suppress('invalid_retry_count')
            return
        }
    }

    if (processingState === 'failed') {
        if (retryCount >= 2) {
            suppress('retry_limit_exhausted')
            return
        }
    }

    if (humanLock === '1') {
        suppress('human_locked')
        return
    }

    if (humanLock !== '0') {
        suppress('invalid_human_lock')
        return
    }

    const eventType = operation === 'insert' ? 'incident.created' : 'incident.updated'
    const eventId = gs.generateGUID()

    gs.info('S1.3 eligibility passed: ' + eventType + ' event_id=' + eventId)
    gs.eventQueue('x_2215032_ai_inc_0.s1_3_outbound_event', current, eventId, eventType)
}
