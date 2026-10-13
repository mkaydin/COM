(() => {
  window.COM = window.COM || {};

  // Derive the WebSocket endpoint from the HTTP base that api.js already
  // resolved, so Electron's dynamic backend port is respected and there is
  // only one source of truth for it.
  async function wsBase() {
    if (window.COM.ready) await window.COM.ready;
    const base = window.COM_API_BASE || 'http://localhost:8734';
    return base.replace(/^http/, 'ws');
  }

  window.COM.connectStream = function(sessionId, handlers = {}) {
    let url = null;
    const urlReady = wsBase().then(b => { url = `${b}/ws/sessions/${sessionId}/stream`; });

    let ws = null;
    let reconnectTimer = null;
    let reconnectAttempts = 0;
    let shouldReconnect = true;

    const on = handlers;
    const emit = (event, data) => {
      const el = document.body;
      el.dispatchEvent(new CustomEvent(`com:${event}`, { detail: data }));
    };

    function connect() {
      if (!url) return; // still resolving the port; a reconnect will pick it up
      ws = new WebSocket(url);

      ws.onopen = () => {
        reconnectAttempts = 0;
        emit('connected', { sessionId });
        on.onConnected?.();
      };

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);

        switch (msg.type) {
          case 'deliberation_started':
            on.onDeliberationStarted?.(msg.deliberation_id, msg.query);
            emit('deliberation_started', msg);
            break;
          case 'router_update':
            on.onRouterUpdate?.(msg.query_type);
            emit('router_update', msg);
            break;
          case 'agent_update':
            on.onAgentUpdate?.(msg.agent, msg.status, msg.response, msg.confidence);
            emit('agent_update', msg);
            break;
          case 'agent_response':
            on.onAgentResponse?.(msg.agent, msg.response);
            emit('agent_response', msg);
            break;
          case 'moderator_summary':
            on.onModeratorSummary?.(msg.content);
            emit('moderator_summary', msg);
            break;
          case 'companion_note':
            on.onCompanionNote?.(msg.content);
            emit('companion_note', msg);
            break;
          case 'critique_round':
            on.onCritiqueRound?.(msg.round, msg.critiques);
            emit('critique_round', msg);
            break;
          case 'error':
            on.onError?.(msg.message);
            emit('error', msg);
            break;
          case 'deliberation_complete':
            on.onComplete?.(msg.deliberation_id);
            emit('deliberation_complete', msg);
            break;
          case 'verdict_saved':
            on.onVerdictSaved?.(msg.verdict_type);
            emit('verdict_saved', msg);
            break;
        }
      };

      ws.onclose = () => {
        ws = null;
        emit('disconnected', {});
        on.onDisconnected?.();

        if (shouldReconnect) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
          reconnectAttempts++;
          reconnectTimer = setTimeout(connect, delay);
          emit('reconnecting', { attempt: reconnectAttempts, delay });
          on.onReconnecting?.(reconnectAttempts, delay);
        }
      };

      ws.onerror = (err) => {
        on.onConnectionError?.(err);
      };
    }

    connect();
    // If the port had not resolved yet, connect for real once it has.
    urlReady.then(() => { if (!ws) connect(); });

    return {
      sendQuery(query, targetAgent = null) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          const msg = { type: 'query', content: query };
          if (targetAgent && targetAgent !== 'all') msg.target_agent = targetAgent;
          ws.send(JSON.stringify(msg));
        }
      },

      sendAction(action, payload = {}) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: action, ...payload }));
        }
      },

      close() {
        shouldReconnect = false;
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (ws) ws.close();
      },

      getState() {
        return ws ? ws.readyState : WebSocket.CLOSED;
      }
    };
  };
})();
