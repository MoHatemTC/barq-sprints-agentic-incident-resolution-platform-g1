import { Property, Record, RestMessage, ScriptAction } from '@servicenow/sdk/core'
import { sendS13Event } from '../server/script-actions/send-s1-3-event'

export const s13EventEndpoint = Property({
    $id: Now.ID['s1_3_event_endpoint'],
    name: 'x_2215032_ai_inc_0.s1_3_event_endpoint',
    type: 'string',
    value: '',
    description: 'Endpoint URL for outbound S1.3 incident events. Configure this separately in each instance.',
})

export const outboundEventRegistration = Record({
    $id: Now.ID['s1_3_outbound_event_registration'],
    table: 'sysevent_register',
    data: {
        event_name: 'x_2215032_ai_inc_0.s1_3_outbound_event',
        suffix: 's1_3_outbound_event',
        table: 'incident',
        priority: 100,
        description: 'Queues an eligible S1.3 minimal outbound incident event for asynchronous transport.',
        fired_by: 'AI Incident Orchestrator S1.3 eligibility Business Rule',
    },
})

export const s13RestMessage = RestMessage({
    $id: Now.ID['s1_3_outbound_rest_message'],
    name: 'AI Incident Orchestrator S1.3 Event',
    endpoint: '',
    description: 'Runtime-configured transport for the minimal S1.3 event.',
    access: 'packagePrivate',
    authenticationType: 'noAuthentication',
    headers: [
        {
            $id: Now.ID['s1_3_content_type_header'],
            name: 'Content-Type',
            value: 'application/json',
        },
    ],
    functions: [
        {
            name: 'post',
            httpMethod: 'POST',
            authenticationType: 'inheritFromParent',
        },
    ],
})

export const outboundEventScriptAction = ScriptAction({
    $id: Now.ID['s1_3_outbound_event_script_action'],
    name: 'AI Incident Orchestrator - Send S1.3 Event',
    eventName: 'x_2215032_ai_inc_0.s1_3_outbound_event',
    order: 100,
    active: true,
    description: 'Reconstructs and sends the exact four-field S1.3 payload asynchronously.',
    script: sendS13Event,
})
