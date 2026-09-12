import { gs } from '@servicenow/glide'

const PROCESSING_STATE = 'x_2215032_ai_inc_0_ai_processing_state'
const RETRY_COUNT = 'x_2215032_ai_inc_0_ai_retry_count'
const HUMAN_REVIEW_REQUIRED = 'x_2215032_ai_inc_0_ai_human_review_required'
const SUPPORTED_CATEGORIES = ['software', 'hardware', 'network', 'database']

export function escalateExhaustedRetry(current: any, previous: any): void {
    if (current.getValue(PROCESSING_STATE) !== 'failed') {
        return
    }

    if (!previous || previous.getValue(PROCESSING_STATE) === 'failed') {
        return
    }

    const retryValue = current.getValue(RETRY_COUNT)
    const retryCount = parseInt(retryValue || '0', 10)

    if (isNaN(retryCount) || retryCount < 0) {
        current.setValue(HUMAN_REVIEW_REQUIRED, '1')
        gs.info('S1.3 retry escalation: invalid_retry_count')
        return
    }

    if (retryCount >= 2) {
        current.setValue(HUMAN_REVIEW_REQUIRED, '1')
        gs.info('S1.3 retry escalation: retry_limit_exhausted')
        return
    }

    if (
        current.getValue('active') !== '1' ||
        current.getValue('x_2215032_ai_inc_0_ai_enabled') !== '1' ||
        SUPPORTED_CATEGORIES.indexOf(current.getValue('category')) === -1 ||
        current.getValue('x_2215032_ai_inc_0_ai_human_lock') === '1'
    ) {
        return
    }

    const nextRetryCount = retryCount + 1
    current.setValue(RETRY_COUNT, String(nextRetryCount))
    gs.info('S1.3 retry count advanced: ' + retryCount + ' -> ' + nextRetryCount)
}
