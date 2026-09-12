import { gs } from '@servicenow/glide'

declare const sn_ws: {
    RESTMessageV2: new (messageName: string, methodName: string) => {
        setRequestBody(content: string): void
        execute(): { getStatusCode(): number }
    }
}

export function sendS13Event(current: any, event: any): void {
    try {
        const outboundEvent = {
            event_id: String(event.parm1),
            sys_id: current.getUniqueValue(),
            number: current.getValue('number'),
            event_type: String(event.parm2),
        }

        const request = new sn_ws.RESTMessageV2('AI Incident Orchestrator S1.3 Event', 'post')
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
