// Script include BarqControl: when each BARQ button applies to an incident.
// UI action conditions are cut at 254 characters, so the rules live here and every
// condition is one short call, e.g. new x_2215032_ai_inc_0.BarqControl().canTakeOver(current).
var BarqControl = Class.create();
BarqControl.prototype = {
    P: 'x_2215032_ai_inc_0_ai_',

    initialize: function() {},

    _flag: function(record, field) {
        return String(record.getValue(this.P + field)) == '1';
    },
    _aiState: function(record) {
        return String(record.getValue(this.P + 'processing_state') || '');
    },
    _engineer: function() {
        return gs.hasRole('itil') || gs.hasRole('x_2215032_ai_inc_0.operator');
    },
    _approver: function() {
        return gs.hasRole('x_2215032_ai_inc_0.operator');
    },

    // A paused run waits for an approver.
    canDecide: function(record) {
        return this._aiState(record) == 'awaiting_approval' && this._approver();
    },

    // While BARQ AI Agent owns the incident (open, not locked, not handed to people), or
    // while its run is paused for approval (taking over rejects that run first).
    canTakeOver: function(record) {
        var state = this._aiState(record);
        var agentOwns = !this._flag(record, 'human_review_required') && state != 'failed';
        return record.active == true && !this._flag(record, 'human_lock') &&
            (agentOwns || state == 'awaiting_approval') && this._engineer();
    },

    // Engineers own it (taken over, failed, or the fix was handed to them) and it is open.
    canHandBack: function(record) {
        if (record.active != true || String(record.getValue('state')) == '6') return false;
        if (!this._flag(record, 'enabled') || !this._engineer()) return false;
        var state = this._aiState(record);
        return this._flag(record, 'human_lock') || state == 'failed' || state == 'complete';
    },

    type: 'BarqControl'
};
