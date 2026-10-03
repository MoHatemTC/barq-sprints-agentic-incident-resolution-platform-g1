// Business rule "BARQ AI - Conversation events" (incident, before update, order 1000).
// Turns what people do on an incident into contract v2 events for the BARQ backend, and
// decides who is in control of the caller conversation (design 2A):
//   - the caller replies or edits while the agent owns the incident -> back to the agent
//     (AI state pending; On Hold - Awaiting Caller returns to In Progress);
//   - the caller replies on an AI-resolved incident, or it is reopened -> an engineer
//     takes over with the AI's fix in a work note (Human Lock on);
//   - someone other than the caller writes to the caller -> a person takes the
//     conversation (Human Lock on), so the caller never hears two voices;
//   - the incident is closed -> recorded for learning.
// The agent's own writes never trigger anything. Runs late (order 1000) so stock rules
// that change the state run first.
(function executeRule(current, previous) {
    var P = 'x_2215032_ai_inc_0_ai_';
    if (current.getValue(P + 'enabled') != '1') return;
    var agentUser = gs.getProperty('x_2215032_ai_inc_0.agent_user_name', 'ai_orchestrator_svc');
    if (gs.getUserName() == agentUser) return;

    var actor = gs.getUserID();
    var isCaller = actor == current.getValue('caller_id');
    var wrote = current.comments.changes();
    var edited = current.short_description.changes() || current.description.changes();
    var before = previous.getValue('state');
    var state = current.getValue('state');
    var aiState = current.getValue(P + 'processing_state');
    var locked = current.getValue(P + 'human_lock') == '1';
    var resolvedByAgent = previous.resolved_by.user_name == agentUser;
    var event = '';

    // A reply on an incident the agent resolved means the fix did not work.
    if (isCaller && wrote && before == '6' && state == '6' && resolvedByAgent) {
        current.state = 2;
        state = '2';
    }

    if (before == '6' && state != '6' && state != '7') {
        if (resolvedByAgent) {
            current.setValue(P + 'human_lock', true);
            current.setValue(P + 'human_review_required', true);
            current.work_notes = 'Reopened by ' + gs.getUserDisplayName() +
                ' after a BARQ AI Agent resolution, so an engineer takes over. The AI fix was:\n' +
                (current.getValue(P + 'resolution') || '(none recorded)');
        }
        event = 'incident.reopened';
    } else if (state == '7' && before != '7') {
        event = 'incident.closed';
    } else if (wrote && !isCaller) {
        if (!locked) {
            current.setValue(P + 'human_lock', true);
            current.work_notes = 'BARQ AI Agent stood down: ' + gs.getUserDisplayName() +
                ' is talking with the caller. Use "Hand back to BARQ AI" to return the incident to the agent.';
        }
        event = 'incident.engineer_replied';
    } else if (isCaller && (wrote || edited)) {
        var waiting = aiState == 'in_progress' && state == '3' && current.getValue('hold_reason') == '1';
        var owned = !locked && current.active == true && state != '6' && state != '7' &&
            (aiState == 'complete' || aiState == 'failed' || waiting);
        if (owned) {
            current.setValue(P + 'processing_state', 'pending');
            current.setValue(P + 'failure_reason', '');
            current.setValue(P + 'processing_start', '');
            current.setValue(P + 'processing_end', '');
            if (waiting) {
                current.state = 2;
                current.hold_reason = '';
            }
        }
        event = wrote ? 'incident.caller_replied' : 'incident.caller_updated';
    }

    if (event)
        gs.eventQueue('x_2215032_ai_inc_0.barq_event', current, gs.generateGUID(), event + '|' + actor);
})(current, previous);
