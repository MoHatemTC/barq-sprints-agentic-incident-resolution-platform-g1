// UI action "Reject AI fix" (incident form). Condition: new x_2215032_ai_inc_0.BarqControl().canDecide(current)
(function() {
    // The condition is checked again here: a button must never act where it does not apply.
    if (!new x_2215032_ai_inc_0.BarqControl().canDecide(current)) {
        gs.addErrorMessage('This action is not available for this incident now.');
        action.setRedirectURL(current);
        return;
    }
    var result = new BarqBackend().decide(current, 'rejected');
    if (result.ok)
        gs.addInfoMessage(result.message);
    else
        gs.addErrorMessage(result.message);
    action.setRedirectURL(current);
})();
