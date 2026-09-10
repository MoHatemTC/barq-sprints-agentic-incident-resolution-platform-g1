const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const GENERATED_MODULE_ID = 'a5815c8f3d0b4f93ab425f3e4498f103'
const GENERATED_MODULE_PATH = path.resolve(
    __dirname,
    '..',
    'dist',
    'app',
    'update',
    `sys_module_${GENERATED_MODULE_ID}.xml`
)

function loadEvaluator() {
    const xml = fs.readFileSync(GENERATED_MODULE_PATH, 'utf8')
    const content = xml.match(/<content><!\[CDATA\[([\s\S]*?)\]\]><\/content>/)
    assert.ok(content, 'Generated eligibility module content must exist; run npm run build first')

    const logs = []
    let guidSequence = 0
    const context = {
        __gs: {
            generateGUID: () => `event-${++guidSequence}`,
            info: (message) => logs.push(message),
        },
    }
    const runnable =
        content[1]
            .replace("import { gs } from '@servicenow/glide';", 'const gs = globalThis.__gs;')
            .replace('export function evaluateIncidentEligibility', 'function evaluateIncidentEligibility') +
        '\nglobalThis.__evaluate = evaluateIncidentEligibility;'

    vm.runInNewContext(runnable, context)
    return { evaluate: context.__evaluate, logs }
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
}

function record(operation, overrides = {}) {
    const values = { ...BASE_VALUES, ...overrides }
    return {
        getUniqueValue: () => values.sys_id,
        getValue: (field) => values[field] ?? null,
        operation: () => operation,
    }
}

function plain(value) {
    return value === null ? null : JSON.parse(JSON.stringify(value))
}

test('eligible insert prepares exactly the v1 incident.created contract', () => {
    const { evaluate } = loadEvaluator()
    const event = plain(evaluate(record('insert'), null))

    assert.deepEqual(event, {
        event_id: 'event-1',
        sys_id: BASE_VALUES.sys_id,
        number: BASE_VALUES.number,
        event_type: 'incident.created',
    })
    assert.deepEqual(Object.keys(event), ['event_id', 'sys_id', 'number', 'event_type'])
})

test('eligible relevant update prepares incident.updated', () => {
    const { evaluate } = loadEvaluator()
    const event = plain(evaluate(record('update', { category: 'hardware' }), record('update')))

    assert.equal(event.event_type, 'incident.updated')
    assert.equal(event.sys_id, BASE_VALUES.sys_id)
    assert.equal(event.number, BASE_VALUES.number)
})

test('separate eligible emissions receive different event IDs', () => {
    const { evaluate } = loadEvaluator()
    const first = plain(evaluate(record('insert'), null))
    const second = plain(evaluate(record('insert'), null))

    assert.notEqual(first.event_id, second.event_id)
})

test('suppressed incident prepares no event and consumes no event ID', () => {
    const { evaluate } = loadEvaluator()
    const suppressed = plain(evaluate(record('insert', { active: '0' }), null))
    const eligible = plain(evaluate(record('insert'), null))

    assert.equal(suppressed, null)
    assert.equal(eligible.event_id, 'event-1')
})

test('irrelevant update prepares no event and consumes no event ID', () => {
    const { evaluate, logs } = loadEvaluator()
    const event = plain(
        evaluate(record('update', { short_description: 'After' }), record('update', { short_description: 'Before' }))
    )
    const eligible = plain(evaluate(record('insert'), null))

    assert.equal(event, null)
    assert.equal(logs.length, 1)
    assert.equal(eligible.event_id, 'event-1')
})

test('failed remains eligible', () => {
    const { evaluate } = loadEvaluator()
    const event = plain(evaluate(record('insert', { x_2215032_ai_inc_0_ai_processing_state: 'failed' }), null))

    assert.equal(event.event_type, 'incident.created')
})

test('malformed processing state prepares no event', () => {
    const { evaluate } = loadEvaluator()
    const event = evaluate(record('insert', { x_2215032_ai_inc_0_ai_processing_state: 'corrupt_state' }), null)

    assert.equal(event, null)
})

test('Human Lock true prepares no event', () => {
    const { evaluate } = loadEvaluator()
    const event = evaluate(record('insert', { x_2215032_ai_inc_0_ai_human_lock: '1' }), null)

    assert.equal(event, null)
})

test('unsupported category prepares no event', () => {
    const { evaluate } = loadEvaluator()
    const event = evaluate(record('insert', { category: 'password_reset' }), null)

    assert.equal(event, null)
})
