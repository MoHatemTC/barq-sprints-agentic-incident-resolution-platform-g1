// =============================================================================
// S1.3 — AI Incident Orchestrator: eligibility evaluation + outbound event
// =============================================================================
// DRAFT SCAFFOLD. Owner: Ahmed Tamer (S1.3). Verify every assumption against
// the PDI before submitting — this has not been run on an instance.
//
// Table:    Incident [incident]
// When:     after
// Insert:   checked
// Update:   checked
// Order:    1000  (run late, after other after-rules have settled)
//
// Covers FR-03 (eligibility on insert and relevant update) and FR-04 (the
// outbound event carries identifiers only).
//
// Scope note: this rule must be created while AI Incident Orchestrator is the
// active application so it exports inside the scoped update set. A scoped app
// cannot use a *before* rule to abort writes on the global Incident table, but
// an *after* rule that reads and emits is fine. It needs a cross-scope
// privilege record for incident read access, which S1.1 already established.
// =============================================================================

(function executeRule(current, previous /*null when async*/) {

    // -- Configuration -------------------------------------------------------
    // Kept in system properties so the endpoint and category list change
    // without editing and re-exporting the rule.
    var ENDPOINT_PROPERTY  = 'x_2215032_ai_inc_0.event_endpoint';
    var CATEGORY_PROPERTY  = 'x_2215032_ai_inc_0.supported_categories';
    var DEFAULT_CATEGORIES = 'hardware,software,network,inquiry';

    var F = {
        enabled:   'x_2215032_ai_inc_0_ai_enabled',
        state:     'x_2215032_ai_inc_0_ai_processing_state',
        humanLock: 'x_2215032_ai_inc_0_ai_human_lock'
    };

    // Fields whose change should re-trigger evaluation on update. Deliberately
    // excludes every AI-written field: if the orchestrator's own write-back
    // could re-trigger this rule, one incident would emit events forever.
    var RELEVANT_FIELDS = ['short_description', 'description', 'category',
                           'subcategory', 'active', F.enabled];

    // -- Eligibility: all six conditions from FR-03 ---------------------------
    // Each returns a reason string when INELIGIBLE, or null when it passes,
    // so a rejection can be logged with the exact condition that failed.
    function ineligibilityReason() {
        if (!current.active) {
            return 'incident is inactive';
        }
        if (current.getValue(F.enabled) !== '1') {
            return 'AI processing is not enabled';
        }
        if (current.getValue(F.humanLock) === '1') {
            return 'incident is human-locked';
        }

        var state = current.getValue(F.state);
        if (state === 'in_progress') {
            return 'a run is already in progress';
        }
        if (state === 'complete' || state === 'failed' || state === 'awaiting_approval') {
            return 'incident has already been processed (state: ' + state + ')';
        }

        var supported = (gs.getProperty(CATEGORY_PROPERTY, DEFAULT_CATEGORIES) || '')
            .split(',')
            .map(function (c) { return c.trim().toLowerCase(); })
            .filter(function (c) { return c.length > 0; });

        var category = (current.getValue('category') || '').toLowerCase();
        if (supported.indexOf(category) === -1) {
            return 'category "' + category + '" is not supported';
        }

        return null;
    }

    // -- "Relevant update" ----------------------------------------------------
    function isRelevantChange() {
        if (current.operation() === 'insert') {
            return true;
        }
        for (var i = 0; i < RELEVANT_FIELDS.length; i++) {
            if (current.changes(RELEVANT_FIELDS[i])) {
                return true;
            }
        }
        return false;
    }

    // -- Guard ---------------------------------------------------------------
    if (!isRelevantChange()) {
        return;
    }

    var reason = ineligibilityReason();
    if (reason) {
        // Emitting nothing is the required behaviour. Log it so a silent
        // non-emission is still diagnosable.
        gs.debug('[AI Orchestrator] No event for ' + current.getValue('number') +
                 ' - ' + reason);
        return;
    }

    // -- Emit: identifiers only (FR-04) --------------------------------------
    // The backend retrieves whatever it is authorised to retrieve. The event
    // leaks nothing if intercepted, ServiceNow ACLs stay the single source of
    // truth, and adding an Incident field never changes the event contract.
    var payload = {
        event_id:   gs.generateGUID(),
        sys_id:     current.getUniqueValue(),
        number:     current.getValue('number'),
        event_type: current.operation() === 'insert'
                        ? 'incident.created'
                        : 'incident.updated'
    };

    var endpoint = gs.getProperty(ENDPOINT_PROPERTY, '');
    if (!endpoint) {
        gs.error('[AI Orchestrator] ' + ENDPOINT_PROPERTY +
                 ' is not set; event for ' + payload.number + ' was not sent');
        return;
    }

    try {
        var request = new sn_ws.RESTMessageV2();
        request.setEndpoint(endpoint);
        request.setHttpMethod('POST');
        request.setRequestHeader('Content-Type', 'application/json');

        // OAuth per FR-06. The profile is created by S1.2 and must belong to
        // the least-privilege integration identity - never an admin account.
        // request.setAuthenticationProfile('oauth2', '<oauth_profile_sys_id>');

        request.setRequestBody(global.JSON.stringify(payload));

        // Asynchronous so the Incident transaction never waits on the network.
        request.executeAsync();

        gs.info('[AI Orchestrator] Emitted ' + payload.event_type + ' for ' +
                payload.number + ' (event ' + payload.event_id + ')');
    } catch (ex) {
        gs.error('[AI Orchestrator] Failed to emit event for ' +
                 payload.number + ': ' + ex.message);
    }

})(current, previous);
