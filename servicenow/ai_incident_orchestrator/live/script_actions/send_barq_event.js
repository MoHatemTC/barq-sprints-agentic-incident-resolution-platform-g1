// Script action for the event x_2215032_ai_inc_0.barq_event: sends contract v2 to the
// BARQ backend through the same REST message, endpoint property and OAuth profile as the
// original S1.3 event. parm1 = event_id, parm2 = "<event_type>|<actor sys_id>".
// Identifiers only: no incident text ever leaves ServiceNow in the event.
(function() {
    try {
        var endpoint = String(gs.getProperty('x_2215032_ai_inc_0.s1_3_event_endpoint', '') || '').trim();
        if (!endpoint) {
            gs.error('BARQ event error: endpoint property x_2215032_ai_inc_0.s1_3_event_endpoint is not set');
            return;
        }
        var parts = String(event.parm2 || '').split('|');
        var body = {
            event_id: String(event.parm1),
            sys_id: current.getUniqueValue(),
            number: current.getValue('number'),
            event_type: parts[0],
            contract_version: 'v2'
        };
        if (/^[0-9a-f]{32}$/.test(parts[1] || ''))
            body.actor_sys_id = parts[1];
        var request = new sn_ws.RESTMessageV2('AI Incident Orchestrator S1.3 Event', 'post');
        request.setEndpoint(endpoint);
        request.setRequestBody(JSON.stringify(body));
        var status = request.execute().getStatusCode();
        if (status >= 200 && status < 300)
            gs.info('BARQ event sent: ' + body.event_type + ' ' + body.event_id + ' status=' + status);
        else
            gs.error('BARQ event failed: ' + body.event_type + ' ' + body.event_id + ' status=' + status);
    } catch (error) {
        gs.error('BARQ event error: ' + error.message);
    }
})();
