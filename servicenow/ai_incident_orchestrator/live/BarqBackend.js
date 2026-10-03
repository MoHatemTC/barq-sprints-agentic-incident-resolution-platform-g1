/**
 * BarqBackend — the one server-side bridge from ServiceNow to the BARQ backend.
 *
 * The backend is plain HTTP on a bare IP, so a browser on this HTTPS instance cannot
 * call it directly. ServiceNow calls it from its own servers instead, with the operator
 * credential read from one place (system properties), never from script text. The
 * engineer's own identity travels with every decision.
 *
 * Scope: x_2215032_ai_inc_0. Accessible from this application scope only.
 */
var BarqBackend = Class.create();
BarqBackend.prototype = {
    initialize: function() {
        this.base = String(gs.getProperty('x_2215032_ai_inc_0.ec2_endpoint', '') || '').replace(/\/+$/, '');
        this.clientId = String(gs.getProperty('x_2215032_ai_inc_0.operator_client_id', 'barq-operator') || '');
        this.secret = String(gs.getProperty('x_2215032_ai_inc_0.operator_client_secret', '') || '');
    },

    _token: function() {
        if (!this.base || !this.secret)
            throw new Error('the BARQ backend endpoint or operator credential is not configured');
        var request = new sn_ws.RESTMessageV2();
        request.setEndpoint(this.base + '/api/v1/oauth/token');
        request.setHttpMethod('POST');
        request.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
        request.setRequestHeader('Accept', 'application/json');
        request.setRequestBody('grant_type=client_credentials&client_id=' + encodeURIComponent(this.clientId) +
            '&client_secret=' + encodeURIComponent(this.secret));
        var response = request.execute();
        var status = response.getStatusCode();
        if (status < 200 || status >= 300)
            throw new Error('the BARQ backend refused the operator credential (HTTP ' + status + ')');
        return JSON.parse(response.getBody()).access_token;
    },

    /** Call the backend; returns {status, data}. Never throws for an HTTP error status. */
    call: function(method, path, body) {
        var request = new sn_ws.RESTMessageV2();
        request.setEndpoint(this.base + path);
        request.setHttpMethod(method);
        request.setRequestHeader('Authorization', 'Bearer ' + this._token());
        request.setRequestHeader('Accept', 'application/json');
        if (body) {
            request.setRequestHeader('Content-Type', 'application/json');
            request.setRequestBody(JSON.stringify(body));
        }
        var response = request.execute();
        var data = null;
        try {
            data = JSON.parse(response.getBody());
        } catch (ignored) {
            data = null;
        }
        return {status: response.getStatusCode(), data: data};
    },

    /** The execution of this incident that is paused for a human, or null. */
    pausedExecution: function(incidentSysId) {
        var result = this.call('GET', '/api/v1/incidents/' + incidentSysId + '/executions');
        if (result.status != 200 || !result.data)
            return null;
        var runs = result.data.executions || [];
        for (var i = 0; i < runs.length; i++) {
            if (runs[i].status == 'awaiting_approval')
                return runs[i].execution_id;
        }
        return null;
    },

    /**
     * Record an engineer's decision on the paused run and let the agent resume.
     * The backend writes the incident itself, so nothing here touches the record and a
     * failed call changes nothing in ServiceNow.
     */
    decide: function(incident, decision, why) {
        var execution = this.pausedExecution(incident.getUniqueValue());
        if (!execution)
            return {ok: false, message: 'No paused BARQ AI run was found for this incident.'};
        var who = gs.getUserDisplayName() + ' (' + gs.getUserName() + ')';
        var verb = decision == 'approved' ? 'Approved' : 'Rejected';
        var body = {
            decision: decision,
            reason: (why || verb) + ' in ServiceNow by ' + who,
            evidence: {
                source: 'servicenow',
                actor_user_name: gs.getUserName(),
                actor_sys_id: gs.getUserID()
            }
        };
        // Edit & approve: an engineer who changed the AI Resolution field approves
        // their own text, which also becomes a knowledge-article proposal.
        var edited = String(incident.getValue('x_2215032_ai_inc_0_ai_resolution') || '').trim();
        var suggested = String(incident.getValue('x_2215032_ai_inc_0_ai_suggestion') || '').trim();
        if (decision == 'approved' && edited && edited != suggested)
            body.solution = edited;
        var result = this.call('POST', '/api/v1/approvals/' + execution + '/decide', body);
        if (result.status >= 200 && result.status < 300)
            return {ok: true, message: verb + '. BARQ AI Agent resumed and is applying the decision.'};
        var detail = result.data && result.data.error ? result.data.error.message : 'HTTP ' + result.status;
        return {ok: false, message: 'The decision was not applied: ' + detail};
    },

    type: 'BarqBackend'
};
