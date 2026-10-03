api.controller = function($scope, $timeout, $interval) {
    var c = this;

    // Answers arrive as light markdown with internal chunk references. Users see plain
    // readable text: headings and **bold** become bold, "[KB0009-v2.0::chunk::2]" becomes
    // "KB0009", and each source is listed once. Text is escaped before any markup is added.
    function escape(text) {
        return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function readable(text) {
        return escape(text)
            .replace(/\(cited from (\[[^\]]+\](?:,\s*)?)+\)/g, function(all) {
                var ids = all.match(/KB\d+/g) || [];
                return ids.length ? '(' + unique(ids).join(', ') + ')' : '';
            })
            .replace(/\[(KB\d+)[^\]]*::chunk::\d+\]/g, '$1')
            .replace(/^#{1,6}\s*(.+)$/gm, '<strong>$1</strong>')
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    }
    function unique(values) {
        var seen = {};
        return values.filter(function(v) { return v && !seen[v] && (seen[v] = true); });
    }
    function message(role, content, citations) {
        var sources = unique((citations || []).map(function(s) {
            return s.article_number || s.number || s.title;
        }));
        return {role: role, content: content, html: readable(content), sources: sources};
    }
    c.readable = readable;
    c.messages = (c.data.history || []).map(function(m) {
        return message(m.role, m.content, m.citations);
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
        c.messages.push(message('user', text));
        c.proposal = null;
        scroll();
        call({action: 'send', message: text}).then(function(d) {
            if (d.reply) c.messages.push(message('assistant', d.reply.content, d.reply.citations));
            c.proposal = d.proposal || null;
            scroll();
        });
    };
    c.create = function() {
        var proposal = c.proposal;
        call({action: 'create', proposal: proposal}).then(function(d) {
            c.proposal = null;
            if (d.created) {
                c.messages.push(message('assistant', 'I opened ' + d.created.number + ' for you. BARQ AI Agent is working on it; you will see its answer here.'));
                c.open({sys_id: d.created.sys_id});
            }
            scroll();
        });
    };
    c.open = function(t) {
        c.drafting = false;
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
    c.openTickets = function() {
        return (c.data.tickets || []).filter(function(t) { return !t.closed; });
    };
    c.closedTickets = function() {
        return (c.data.tickets || []).filter(function(t) { return t.closed; });
    };
    c.startNew = function(text) {
        c.ticket = null;
        c.proposal = null;
        c.drafting = true;
        c.draft = {short_description: (text || '').substring(0, 160), description: text || '', category: 'inquiry'};
    };
    // Open a ticket from the last thing the user asked in the chat.
    c.ticketFromChat = function() {
        var mine = c.messages.filter(function(m) { return m.role == 'user'; });
        c.startNew(mine.length ? mine[mine.length - 1].content : '');
    };
    c.createNew = function() {
        if (!c.draft || !c.draft.short_description) return;
        c.proposal = {
            short_description: c.draft.short_description,
            description: c.draft.description || c.draft.short_description,
            category: c.draft.category
        };
        c.drafting = false;
        c.create();
    };
    c.confirmCancel = function() {
        if (window.confirm('Cancel ' + c.ticket.number + '? Nobody will work on it any more.')) c.act('cancel');
    };
    c.close = function() { c.ticket = null; c.drafting = false; scroll(); };
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
