import { Property, Record, RestMessage, ScriptAction } from '@servicenow/sdk/core'
import { sendS13Event } from '../server/script-actions/send-s1-3-event'

export const s13EventEndpoint = Property({
    $id: Now.ID['s1_3_event_endpoint'],
    name: 'x_2215032_ai_inc_0.s1_3_event_endpoint',
    type: 'string',
    value: '',
    ignoreCache: true,
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

// OAuth 2.0 client credentials for the outbound event (#93). The token URL and the
// client secret are instance configuration and deliberately absent here, so a deploy
// leaves them as configured (docs/sprint-1/s1.3-eligibility-rule-and-event/outbound-oauth.md).
export const webhookOAuthProvider = Record({
    $id: Now.ID['s1_3_webhook_oauth_provider'],
    table: 'oauth_entity',
    data: {
        name: 'BARQ Webhook OAuth',
        type: 'oauth_provider',
        client_id: 'barq-servicenow',
        default_grant_type: 'client_credentials',
        send_client_credentials_as: 'request_body_parameter',
        access_token_lifespan: 300,
        active: true,
    },
})

export const webhookOAuthProfile = Record({
    $id: Now.ID['s1_3_webhook_oauth_profile'],
    table: 'oauth_entity_profile',
    data: {
        name: 'BARQ Webhook OAuth default_profile',
        oauth_entity: webhookOAuthProvider,
        grant_type: 'client_credentials',
        default: true,
    },
})

export const s13RestMessage = RestMessage({
    $id: Now.ID['s1_3_outbound_rest_message'],
    name: 'AI Incident Orchestrator S1.3 Event',
    // The SDK requires a non-empty parent endpoint. The real target is read at runtime
    // from the s1_3_event_endpoint property and set via setEndpoint(), so this value is
    // an unroutable placeholder and is never called.
    endpoint: 'https://example.invalid',
    description: 'Runtime-configured transport for the minimal S1.3 event.',
    access: 'packagePrivate',
    authenticationType: 'oauth2',
    // sys_id of webhookOAuthProfile (keys.ts s1_3_webhook_oauth_profile); the SDK takes a plain id here.
    oauthProfile: '0b758576739b4b102aedfed25ab8b782',
    functions: [
        {
            name: 'post',
            httpMethod: 'POST',
            authenticationType: 'inheritFromParent',
            headers: [
                {
                    $id: Now.ID['s1_3_content_type_header'],
                    name: 'Content-Type',
                    value: 'application/json',
                },
            ],
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
