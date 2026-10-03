// Server script of the "BARQ AI" widget (Employee Center page ?id=barq_ai).
// Runs as the logged-in user. Every ticket action is limited to incidents where that
// user is the caller, and ticket writes go through GlideRecordSecure so the user's own
// ACLs apply. The chat goes to the BARQ backend through the BarqBackend bridge with the
// user's sys_id; the browser never reaches the backend.
(function() {
    var P = 'x_2215032_ai_inc_0_ai_';
    var me = gs.getUserID();
    data.user = gs.getUserDisplayName();
    data.error = '';

    function ownTicket(sysId) {
        var gr = new GlideRecordSecure('incident');
        if (!sysId || !gr.get(sysId) || gr.getValue('caller_id') != me) return null;
        return gr;
    }

    function status(gr) {
        var state = gr.getValue('state');
        var ai = gr.getValue(P + 'processing_state');
        if (state == '7') return {label: 'Closed', tone: 'done'};
        if (state == '8') return {label: 'Cancelled', tone: 'done'};
        if (state == '6') return {label: 'Solved - please confirm', tone: 'good'};
        if (state == '3' && gr.getValue('hold_reason') == '1') return {label: 'Waiting for your answer', tone: 'warn'};
        if (ai == 'pending' || ai == 'in_progress') return {label: 'BARQ AI is looking at it', tone: 'info'};
        return {label: 'An engineer is working on it', tone: 'info'};
    }

    function ticketSummary(gr) {
        var s = status(gr);
        return {
            sys_id: gr.getUniqueValue(),
            number: gr.getValue('number'),
            title: gr.getValue('short_description'),
            status: s.label,
            tone: s.tone,
            updated: gr.getDisplayValue('sys_updated_on'),
            can_reply: ['6', '7', '8'].indexOf(gr.getValue('state')) < 0,
            can_confirm: gr.getValue('state') == '6'
        };
    }

    function myTickets() {
        var list = [];
        var gr = new GlideRecordSecure('incident');
        gr.addQuery('caller_id', me);
        gr.addQuery('sys_created_on', '>=', gs.daysAgoStart(60));
        gr.orderByDesc('sys_updated_on');
        gr.setLimit(20);
        gr.query();
        while (gr.next()) list.push(ticketSummary(gr));
        return list;
    }

    function conversation(gr) {
        // Customer-visible comments only, oldest first; never work notes.
        var text = String(gr.getDisplayValue('comments') || '');
        var header = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.+?) \((?:Additional comments|Comments)\)\s*$/gm;
        var marks = [], m;
        while ((m = header.exec(text)) !== null) marks.push({at: m[1], who: m[2], start: m.index, end: header.lastIndex});
        var entries = [];
        for (var i = 0; i < marks.length; i++) {
            var body = text.substring(marks[i].end, i + 1 < marks.length ? marks[i + 1].start : text.length);
            entries.push({
                at: marks[i].at,
                who: marks[i].who,
                mine: marks[i].who == data.user,
                agent: marks[i].who == 'BARQ AI Agent' || marks[i].who.indexOf('AI Orchestrator') == 0,
                text: body.replace(/^\s+|\s+$/g, '')
            });
        }
        entries.sort(function(a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : 0; });
        return entries;
    }

    function backend(method, path, body) {
        try {
            return new BarqBackend().call(method, path, body);
        } catch (e) {
            return {status: 0, data: null, error: String(e.message || e)};
        }
    }

    var action = input && input.action;

    if (!action || action == 'init' || action == 'refresh') {
        data.tickets = myTickets();
        if (!action || action == 'init') {
            var history = backend('GET', '/api/v1/assist/history/' + me + '?limit=40');
            data.history = (history.status == 200 && history.data) ? history.data.messages : [];
            data.chat_available = history.status == 200;
        }
        return;
    }

    if (action == 'send') {
        var message = String(input.message || '').replace(/^\s+|\s+$/g, '').substring(0, 4000);
        if (!message) return;
        var reply = backend('POST', '/api/v1/assist/messages', {
            user_sys_id: me,
            message: message,
            request_id: 'sn-' + gs.generateGUID()
        });
        if (reply.status != 200 || !reply.data) {
            data.error = 'BARQ AI is not available right now. You can still open a ticket.';
            data.proposal = {short_description: message.substring(0, 160), description: message, category: 'inquiry'};
            return;
        }
        var messages = reply.data.turn.messages || [];
        data.reply = messages.length ? messages[messages.length - 1] : null;
        data.proposal = reply.data.ticket_proposal;
        return;
    }

    if (action == 'create') {
        var p = input.proposal || {};
        var categories = ['network', 'software', 'hardware', 'inquiry'];
        var inc = new GlideRecord('incident');
        inc.initialize();
        inc.setValue('caller_id', me);
        inc.setValue('short_description', String(p.short_description || '').substring(0, 160));
        inc.setValue('description', String(p.description || '').substring(0, 4000));
        inc.setValue('category', categories.indexOf(p.category) >= 0 ? p.category : 'inquiry');
        inc.setValue('contact_type', 'self-service');
        inc.setValue('impact', 3);
        inc.setValue('urgency', 3);
        inc.setValue(P + 'enabled', true);
        var created = inc.insert();
        data.created = created ? {sys_id: created, number: inc.getValue('number')} : null;
        if (!created) data.error = 'The ticket could not be created.';
        data.tickets = myTickets();
        return;
    }

    var ticket = ownTicket(input.sys_id);
    if (!ticket) {
        data.error = 'That ticket is not available.';
        return;
    }

    if (action == 'open') {
        data.ticket = ticketSummary(ticket);
        data.ticket.description = ticket.getValue('description');
        data.ticket.conversation = conversation(ticket);
        return;
    }

    if (action == 'reply' || action == 'not_fixed') {
        var text = String(input.text || '').replace(/^\s+|\s+$/g, '').substring(0, 4000);
        if (action == 'not_fixed' && !text) text = 'This did not fix my problem.';
        if (!text) return;
        ticket.comments = text;
        ticket.update();
    } else if (action == 'confirm') {
        // Ownership was checked above; closing a solved ticket is the caller's decision,
        // so it is applied server-side even where the caller cannot edit the state field.
        if (ticket.getValue('state') == '6') {
            var closing = new GlideRecord('incident');
            if (closing.get(ticket.getUniqueValue())) {
                closing.state = 7;
                closing.comments = 'Confirmed by the caller: the problem is fixed.';
                closing.update();
            }
        }
    }
    var fresh = ownTicket(input.sys_id);
    data.ticket = ticketSummary(fresh);
    data.ticket.description = fresh.getValue('description');
    data.ticket.conversation = conversation(fresh);
    data.tickets = myTickets();
})();
