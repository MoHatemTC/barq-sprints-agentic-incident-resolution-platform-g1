// UI action "Hand back to BARQ AI" (incident form). Condition:
//   current.active == true && current.state != 6 &&
//   current.x_2215032_ai_inc_0_ai_enabled == true &&
//   (current.x_2215032_ai_inc_0_ai_human_lock == true ||
//    current.x_2215032_ai_inc_0_ai_processing_state == 'failed' ||
//    current.x_2215032_ai_inc_0_ai_processing_state == 'complete') &&
//   (gs.hasRole('itil') || gs.hasRole('x_2215032_ai_inc_0.operator'))
// Returns the incident to the agent. Whatever the engineer typed in Work notes becomes
// the instruction; the agent continues with the whole conversation as evidence.
(function() {
    var P = 'x_2215032_ai_inc_0_ai_';
    var instruction = String(current.work_notes || '').trim();
    var incident = new GlideRecord('incident');
    if (!incident.get(current.getUniqueValue())) return;
    var wasLocked = incident.getValue(P + 'human_lock') == '1';
    incident.setValue(P + 'human_lock', false);
    incident.setValue(P + 'human_review_required', false);
    incident.setValue(P + 'processing_state', 'pending');
    incident.setValue(P + 'failure_reason', '');
    incident.setValue(P + 'processing_start', '');
    incident.setValue(P + 'processing_end', '');
    // The agent does not work an incident that is On Hold, so handing back resumes it.
    if (incident.getValue('state') == '3') {
        incident.state = 2;
        incident.hold_reason = '';
    }
    incident.work_notes = 'Handed back to BARQ AI Agent by ' + gs.getUserDisplayName() +
        (instruction ? '. Instruction: ' + instruction : '.');
    incident.update();
    // Clearing the lock already queues the agent through the eligibility rule; otherwise
    // queue it here so exactly one event is sent.
    if (!wasLocked)
        gs.eventQueue('x_2215032_ai_inc_0.barq_event', incident, gs.generateGUID(),
            'incident.handed_back|' + gs.getUserID());
    gs.addInfoMessage('BARQ AI Agent continues with this incident.');
    action.setRedirectURL(current);
})();
