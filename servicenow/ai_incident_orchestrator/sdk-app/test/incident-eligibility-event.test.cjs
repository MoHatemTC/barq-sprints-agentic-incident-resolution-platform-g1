const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const GENERATED_UPDATE_PATH = path.resolve(__dirname, '..', 'dist', 'app', 'update')

function generatedModule(exportName) {
    const modulePath = fs
        .readdirSync(GENERATED_UPDATE_PATH)
        .filter((name) => name.startsWith('sys_module_') && name.endsWith('.xml'))
        .map((name) => path.join(GENERATED_UPDATE_PATH, name))
        .find((candidate) => fs.readFileSync(candidate, 'utf8').includes(`export function ${exportName}`))

    assert.ok(modulePath, `Generated module for ${exportName} must exist; run npm run build first`)
    const xml = fs.readFileSync(modulePath, 'utf8')
    const content = xml.match(/<content><!\[CDATA\[([\s\S]*?)\]\]><\/content>/)
    assert.ok(content, `Generated module content for ${exportName} must exist`)
    return content[1]
}

function loadFunction(exportName, globals = {}) {
    const context = { ...globals }
    const runnable =
        generatedModule(exportName)
            .replace(/^import .*;\r?$/gm, '')
            .replace(`export function ${exportName}`, `function ${exportName}`) +
        `\nglobalThis.__loadedFunction = ${exportName};`

    vm.runInNewContext(runnable, context)
    return context.__loadedFunction
}

function eligibilityHarness() {
    const logs = []
    const queued = []
    let guidSequence = 0
    const gs = {
        eventQueue: (name, current, parm1, parm2) => queued.push({ name, current, parm1, parm2 }),
        generateGUID: () => `event-${++guidSequence}`,
        info: (message) => logs.push(message),
    }

    return {
        evaluate: loadFunction('evaluateIncidentEligibility', { gs }),
        logs,
        queued,
    }
}

const BASE_VALUES = {
    active: '1',
    category: 'software',
    number: 'INC0012345',
    short_description: 'Before',
    sys_id: '0123456789abcdef0123456789abcdef',
    x_2215032_ai_inc_0_ai_enabled: '1',
    x_2215032_ai_inc_0_ai_human_lock: '0',
    x_2215032_ai_inc_0_ai_processing_state: 'pending',
    x_2215032_ai_inc_0_ai_retry_count: '0',
}

function record(operation, overrides = {}) {
    const values = { ...BASE_VALUES, ...overrides }
    return {
        getUniqueValue: () => values.sys_id,
        getValue: (field) => values[field] ?? null,
        operation: () => operation,
        setValue: (field, value) => {
            values[field] = value
        },
        update: () => assert.fail('current.update() must not be called'),
    }
}

test('eligible insert queues incident.created with event id in parm1 and event type in parm2', () => {
    const { evaluate, queued } = eligibilityHarness()
    const current = record('insert')

    evaluate(current, null)

    assert.deepEqual(queued, [
        {
            name: 'x_2215032_ai_inc_0.s1_3_outbound_event',
            current,
            parm1: 'event-1',
            parm2: 'incident.created',
        },
    ])
})

test('eligible relevant update queues incident.updated', () => {
    const { evaluate, queued } = eligibilityHarness()

    evaluate(record('update', { category: 'hardware' }), record('update'))

    assert.equal(queued.length, 1)
    assert.equal(queued[0].parm2, 'incident.updated')
})

test('each eligible emission receives a unique event id', () => {
    const { evaluate, queued } = eligibilityHarness()

    evaluate(record('insert'), null)
    evaluate(record('insert'), null)

    assert.equal(queued.length, 2)
    assert.notEqual(queued[0].parm1, queued[1].parm1)
})

test('all six approved relevant update fields can trigger an event', () => {
    const changes = {
        active: ['0', '1'],
        category: ['network', 'hardware'],
        x_2215032_ai_inc_0_ai_enabled: ['0', '1'],
        x_2215032_ai_inc_0_ai_processing_state: ['failed', 'pending'],
        x_2215032_ai_inc_0_ai_human_lock: ['1', '0'],
        x_2215032_ai_inc_0_ai_retry_count: ['1', '0'],
    }

    for (const [field, [before, after]] of Object.entries(changes)) {
        const { evaluate, queued } = eligibilityHarness()
        evaluate(record('update', { [field]: after }), record('update', { [field]: before }))
        assert.equal(queued.length, 1, `${field} should be relevant`)
    }
})

test('irrelevant update queues no event and consumes no event id', () => {
    const { evaluate, queued } = eligibilityHarness()

    evaluate(record('update', { short_description: 'After' }), record('update'))
    evaluate(record('insert'), null)

    assert.equal(queued.length, 1)
    assert.equal(queued[0].parm1, 'event-1')
})

for (const [previousRetryCount, advancedRetryCount] of [['0', '1'], ['1', '2']]) {
    test(`new failed transition advances retry_count ${previousRetryCount} -> ${advancedRetryCount} and emits`, () => {
        const retryLogs = []
        const escalate = loadFunction('escalateExhaustedRetry', {
            gs: { info: (message) => retryLogs.push(message) },
        })
        const { evaluate, queued } = eligibilityHarness()
        const previous = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'pending',
            x_2215032_ai_inc_0_ai_retry_count: previousRetryCount,
        })
        const current = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'failed',
            x_2215032_ai_inc_0_ai_retry_count: previousRetryCount,
        })

        escalate(current, previous)
        evaluate(current, previous)

        assert.equal(current.getValue('x_2215032_ai_inc_0_ai_retry_count'), advancedRetryCount)
        assert.deepEqual(retryLogs, [
            `S1.3 retry count advanced: ${previousRetryCount} -> ${advancedRetryCount}`,
        ])
        assert.equal(queued.length, 1)
        assert.equal(queued[0].parm2, 'incident.updated')
    })
}

test('pending remains eligible regardless of an exhausted retry counter', () => {
    const { evaluate, queued } = eligibilityHarness()

    evaluate(record('insert', {
        x_2215032_ai_inc_0_ai_processing_state: 'pending',
        x_2215032_ai_inc_0_ai_retry_count: '2',
    }), null)

    assert.equal(queued.length, 1)
})

test('new failed transition at retry_count 2 emits nothing and requires human review', () => {
    const retryLogs = []
    const escalate = loadFunction('escalateExhaustedRetry', {
        gs: { info: (message) => retryLogs.push(message) },
    })
    const { evaluate, logs, queued } = eligibilityHarness()
    const previous = record('update', {
        x_2215032_ai_inc_0_ai_processing_state: 'pending',
        x_2215032_ai_inc_0_ai_retry_count: '2',
    })
    const current = record('update', {
        x_2215032_ai_inc_0_ai_processing_state: 'failed',
        x_2215032_ai_inc_0_ai_retry_count: '2',
    })

    escalate(current, previous)
    evaluate(current, previous)

    assert.equal(queued.length, 0)
    assert.equal(current.getValue('x_2215032_ai_inc_0_ai_human_review_required'), '1')
    assert.deepEqual(retryLogs, ['S1.3 retry escalation: retry_limit_exhausted'])
    assert.deepEqual(logs, ['S1.3 eligibility suppressed: retry_limit_exhausted'])
})

test('invalid retry count fails closed without an event', () => {
    for (const retryCount of ['not-a-number', '-1']) {
        const retryLogs = []
        const escalate = loadFunction('escalateExhaustedRetry', {
            gs: { info: (message) => retryLogs.push(message) },
        })
        const { evaluate, logs, queued } = eligibilityHarness()
        const previous = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'pending',
            x_2215032_ai_inc_0_ai_retry_count: retryCount,
        })
        const current = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'failed',
            x_2215032_ai_inc_0_ai_retry_count: retryCount,
        })

        escalate(current, previous)
        evaluate(current, previous)

        assert.equal(queued.length, 0)
        assert.equal(current.getValue('x_2215032_ai_inc_0_ai_human_review_required'), '1')
        assert.deepEqual(retryLogs, ['S1.3 retry escalation: invalid_retry_count'])
        assert.deepEqual(logs, ['S1.3 eligibility suppressed: invalid_retry_count'])
    }
})

test('failed to failed unrelated or relevant updates do not emit another retry', () => {
    const updates = [
        [{ short_description: 'After' }, { short_description: 'Before' }],
        [{ category: 'hardware' }, { category: 'software' }],
    ]

    for (const [currentOverrides, previousOverrides] of updates) {
        const retryLogs = []
        const escalate = loadFunction('escalateExhaustedRetry', {
            gs: { info: (message) => retryLogs.push(message) },
        })
        const { evaluate, queued } = eligibilityHarness()
        const current = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'failed',
            x_2215032_ai_inc_0_ai_retry_count: '1',
            ...currentOverrides,
        })
        const previous = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'failed',
            x_2215032_ai_inc_0_ai_retry_count: '1',
            ...previousOverrides,
        })

        escalate(current, previous)
        evaluate(current, previous)

        assert.equal(current.getValue('x_2215032_ai_inc_0_ai_retry_count'), '1')
        assert.equal(retryLogs.length, 0)
        assert.equal(queued.length, 0)
    }
})

test('new failed transition consumes no retry when another eligibility condition fails', () => {
    const ineligibleOverrides = [
        { active: '0' },
        { x_2215032_ai_inc_0_ai_enabled: '0' },
        { category: 'password_reset' },
        { x_2215032_ai_inc_0_ai_human_lock: '1' },
    ]

    for (const overrides of ineligibleOverrides) {
        const logs = []
        const escalate = loadFunction('escalateExhaustedRetry', {
            gs: { info: (message) => logs.push(message) },
        })
        const previous = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'pending',
            x_2215032_ai_inc_0_ai_retry_count: '0',
        })
        const current = record('update', {
            x_2215032_ai_inc_0_ai_processing_state: 'failed',
            x_2215032_ai_inc_0_ai_retry_count: '0',
            ...overrides,
        })

        escalate(current, previous)

        assert.equal(current.getValue('x_2215032_ai_inc_0_ai_retry_count'), '0')
        assert.equal(logs.length, 0)
    }
})

test('every suppression outcome queues no event', () => {
    const suppressed = [
        [{ active: '0' }, 'inactive'],
        [{ x_2215032_ai_inc_0_ai_enabled: '0' }, 'ai_disabled'],
        [{ x_2215032_ai_inc_0_ai_processing_state: 'complete' }, 'already_processed'],
        [{ category: 'password_reset' }, 'unsupported_category'],
        [{ x_2215032_ai_inc_0_ai_processing_state: 'in_progress' }, 'already_running'],
        [{ x_2215032_ai_inc_0_ai_processing_state: 'awaiting_approval' }, 'awaiting_approval'],
        [{ x_2215032_ai_inc_0_ai_processing_state: 'corrupt_state' }, 'invalid_processing_state'],
        [{ x_2215032_ai_inc_0_ai_human_lock: '1' }, 'human_locked'],
        [{ x_2215032_ai_inc_0_ai_human_lock: 'malformed' }, 'invalid_human_lock'],
    ]

    for (const [overrides, reason] of suppressed) {
        const { evaluate, logs, queued } = eligibilityHarness()
        evaluate(record('insert', overrides), null)
        assert.equal(queued.length, 0, reason)
        assert.deepEqual(logs, [`S1.3 eligibility suppressed: ${reason}`])
    }
})

test('Script Action reconstructs and sends exactly the four-field payload', () => {
    const requests = []
    class FakeRESTMessageV2 {
        constructor(messageName, methodName) {
            this.messageName = messageName
            this.methodName = methodName
            requests.push(this)
        }
        setRequestBody(body) {
            this.body = body
        }
        execute() {
            return { getStatusCode: () => 200 }
        }
    }
    const gs = { info: () => {}, error: (message) => assert.fail(message) }
    const send = loadFunction('sendS13Event', { gs, sn_ws: { RESTMessageV2: FakeRESTMessageV2 } })

    send(record('insert'), { parm1: { toString: () => 'event-from-parm1' }, parm2: 'incident.created' })

    assert.equal(requests.length, 1)
    assert.equal(requests[0].messageName, 'AI Incident Orchestrator S1.3 Event')
    assert.equal(requests[0].methodName, 'post')
    assert.deepEqual(JSON.parse(requests[0].body), {
        event_id: 'event-from-parm1',
        sys_id: BASE_VALUES.sys_id,
        number: BASE_VALUES.number,
        event_type: 'incident.created',
    })
    assert.deepEqual(Object.keys(JSON.parse(requests[0].body)), ['event_id', 'sys_id', 'number', 'event_type'])
})

test('retry escalation sets human review before update without calling current.update()', () => {
    const logs = []
    const escalate = loadFunction('escalateExhaustedRetry', { gs: { info: (message) => logs.push(message) } })
    const current = record('update', {
        x_2215032_ai_inc_0_ai_processing_state: 'failed',
        x_2215032_ai_inc_0_ai_retry_count: '2',
    })
    const previous = record('update', {
        x_2215032_ai_inc_0_ai_processing_state: 'pending',
        x_2215032_ai_inc_0_ai_retry_count: '2',
    })

    escalate(current, previous)

    assert.equal(current.getValue('x_2215032_ai_inc_0_ai_human_review_required'), '1')
    assert.deepEqual(logs, ['S1.3 retry escalation: retry_limit_exhausted'])
})
