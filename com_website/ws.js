(() => {
  window.COM = window.COM || {};

  window.COM.connectStream = function(sessionId, handlers = {}) {
    const port = window.electronAPI?.getBackendPort?.() || 8734;
    const url = `ws://localhost:${port}/ws/sessions/${sessionId}/stream`;

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
      ws = new WebSocket(url);

      ws.onopen = () => {
        reconnectAttempts = 0;
        emit('connected', { sessionId });
        on.onConnected?.();
      };

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);

        switch (msg.type) {
          case 'agent_update':
            on.onAgentUpdate?.(msg.agent, msg.status, msg.partial, msg.confidence, msg.sources);
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
            emit('complete', msg);
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

    return {
      sendQuery(query) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'query', content: query }));
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
