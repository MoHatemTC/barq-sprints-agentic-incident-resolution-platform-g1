api.controller = function($scope, $timeout, $interval) {
    var c = this;
    c.messages = (c.data.history || []).map(function(m) {
        return {role: m.role, content: m.content, citations: m.citations || []};
    });
    c.ticket = null;
    c.proposal = null;
    c.text = '';
    c.busy = false;

    function scroll() {
        $timeout(function() {
            var el = document.getElementById('barq-thread');
            if (el) el.scrollTop = el.scrollHeight;
        });
    }
    function call(params) {
        c.busy = true;
        c.data.error = '';
        return c.server.get(params).then(function(response) {
            c.busy = false;
            c.data.error = response.data.error;
            if (response.data.tickets) c.data.tickets = response.data.tickets;
            return response.data;
        }, function() {
            c.busy = false;
            c.data.error = 'Something went wrong. Please try again.';
            return {};
        });
    }

    c.send = function() {
        var text = (c.text || '').trim();
        if (!text || c.busy) return;
        c.text = '';
        if (c.ticket) {
            call({action: 'reply', sys_id: c.ticket.sys_id, text: text}).then(function(d) {
                if (d.ticket) c.ticket = d.ticket;
                scroll();
            });
            return;
        }
        c.messages.push({role: 'user', content: text, citations: []});
        c.proposal = null;
        scroll();
        call({action: 'send', message: text}).then(function(d) {
            if (d.reply) c.messages.push({role: 'assistant', content: d.reply.content, citations: d.reply.citations || []});
            c.proposal = d.proposal || null;
            scroll();
        });
    };
    c.create = function() {
        var proposal = c.proposal;
        call({action: 'create', proposal: proposal}).then(function(d) {
            c.proposal = null;
            if (d.created) {
                c.messages.push({role: 'assistant', content: 'I opened ' + d.created.number + ' for you. BARQ AI Agent is working on it; you will see its answer here.', citations: []});
                c.open({sys_id: d.created.sys_id});
            }
            scroll();
        });
    };
    c.open = function(t) {
        call({action: 'open', sys_id: t.sys_id}).then(function(d) {
            if (d.ticket) c.ticket = d.ticket;
            scroll();
        });
    };
    c.act = function(kind) {
        call({action: kind, sys_id: c.ticket.sys_id}).then(function(d) {
            if (d.ticket) c.ticket = d.ticket;
            scroll();
        });
    };
    c.close = function() { c.ticket = null; scroll(); };
    c.refresh = function() {
        call({action: 'refresh'});
        if (c.ticket) c.open(c.ticket);
    };

    // While a ticket is open, pick up new messages from BARQ AI Agent or an engineer.
    var poll = $interval(function() {
        if (c.ticket && !c.busy) {
            c.server.get({action: 'open', sys_id: c.ticket.sys_id}).then(function(response) {
                var t = response.data.ticket;
                if (t && c.ticket && t.sys_id == c.ticket.sys_id &&
                    (t.conversation.length != c.ticket.conversation.length || t.status != c.ticket.status)) {
                    c.ticket = t;
                    scroll();
                }
            });
        }
    }, 8000);
    $scope.$on('$destroy', function() { $interval.cancel(poll); });
    scroll();
};
