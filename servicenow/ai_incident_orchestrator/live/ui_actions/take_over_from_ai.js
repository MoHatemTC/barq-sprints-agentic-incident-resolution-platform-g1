// UI action "Take over from AI" (incident form). Condition:
//   current.x_2215032_ai_inc_0_ai_human_lock != true && current.active == true &&
//   (gs.hasRole('itil') || gs.hasRole('x_2215032_ai_inc_0.operator'))
// A paused AI run is rejected first (the agent can still write its closing note),
// then the Human Lock keeps the agent out of this incident from now on. A fresh copy of
// the record is updated so nothing the agent just wrote is overwritten by form values.
(function() {
    var sysId = current.getUniqueValue();
    if (current.x_2215032_ai_inc_0_ai_processing_state == 'awaiting_approval') {
        var result = new BarqBackend().decide(current, 'rejected', 'Taken over');
        if (!result.ok)
            gs.addInfoMessage('The paused AI run could not be closed (' + result.message + '). The lock is applied anyway.');
    }
    var incident = new GlideRecord('incident');
    if (incident.get(sysId)) {
        incident.x_2215032_ai_inc_0_ai_human_lock = true;
        incident.x_2215032_ai_inc_0_ai_human_review_required = false;
        incident.work_notes = 'Taken over from BARQ AI Agent by ' + gs.getUserDisplayName() +
            '. The agent will not change this incident while the Human Lock is on.';
        incident.update();
        gs.addInfoMessage('You have taken over this incident from BARQ AI Agent.');
    }
    action.setRedirectURL(current);
})();
