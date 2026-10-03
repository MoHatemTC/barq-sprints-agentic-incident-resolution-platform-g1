// UI action "Hand back to BARQ AI" (incident form). Condition: new x_2215032_ai_inc_0.BarqControl().canHandBack(current)
// Returns the incident to the agent. Whatever the engineer typed in Work notes becomes
// the instruction; the agent continues with the whole conversation as evidence.
(function() {
    // The condition is checked again here: a button must never act where it does not apply.
    if (!new x_2215032_ai_inc_0.BarqControl().canHandBack(current)) {
        gs.addErrorMessage('This action is not available for this incident now.');
        action.setRedirectURL(current);
        return;
    }
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
