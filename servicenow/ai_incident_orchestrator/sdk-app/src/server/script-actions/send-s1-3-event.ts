import { gs } from '@servicenow/glide'
import { RESTMessageV2 } from '@servicenow/glide/sn_ws'

// The published typing only models the Java constructor; scripts use (message, method).
type OutboundRequest = {
    setEndpoint(endpoint: string): void
    setRequestBody(content: string): void
    execute(): { getStatusCode(): number }
}
const NamedRESTMessage = RESTMessageV2 as unknown as new (
    messageName: string,
    methodName: string
) => OutboundRequest

export function sendS13Event(current: any, event: any): void {
    try {
        const endpointProperty = 'x_2215032_ai_inc_0.s1_3_event_endpoint'
        const endpoint = String(gs.getProperty(endpointProperty, '') || '').trim()

        if (!endpoint) {
            gs.error('S1.3 outbound event error: endpoint property not set: ' + endpointProperty)
            return
        }

        const outboundEvent = {
            event_id: String(event.parm1),
            sys_id: current.getUniqueValue(),
            number: current.getValue('number'),
            event_type: String(event.parm2),
        }

        const request = new NamedRESTMessage('AI Incident Orchestrator S1.3 Event', 'post')
        request.setEndpoint(endpoint)
        request.setRequestBody(JSON.stringify(outboundEvent))

        const response = request.execute()
        const statusCode = response.getStatusCode()

        if (statusCode >= 200 && statusCode < 300) {
            gs.info('S1.3 outbound event sent: ' + outboundEvent.event_id + ' status=' + statusCode)
        } else {
            gs.error('S1.3 outbound event failed: ' + outboundEvent.event_id + ' status=' + statusCode)
        }
    } catch (error: any) {
        gs.error('S1.3 outbound event error: ' + error.message)
    }
}
