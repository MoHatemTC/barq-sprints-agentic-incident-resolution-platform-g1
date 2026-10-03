// UI action "Reject AI fix" (incident form). Condition:
//   current.x_2215032_ai_inc_0_ai_processing_state == 'awaiting_approval' &&
//   gs.hasRole('x_2215032_ai_inc_0.operator')
(function() {
    var result = new BarqBackend().decide(current, 'rejected');
    if (result.ok)
        gs.addInfoMessage(result.message);
    else
        gs.addErrorMessage(result.message);
    action.setRedirectURL(current);
})();
