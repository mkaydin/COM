(() => {
  window.COM_API_BASE = 'http://localhost:8734';

  if (window.electronAPI?.getBackendPort) {
    Promise.resolve(window.electronAPI.getBackendPort()).then(port => {
      if (typeof port === 'number' && port > 0) {
        window.COM_API_BASE = `http://localhost:${port}`;
      }
    }).catch(() => {});
  }

  window.COM = {
    async api(path, opts = {}) {
      const url = `${window.COM_API_BASE}${path}`;
      let res;
      try {
        res = await fetch(url, {
          headers: { 'Content-Type': 'application/json', ...opts.headers },
          ...opts
        });
      } catch (netErr) {
        throw new Error(`Cannot reach backend at ${window.COM_API_BASE} — is the server running? (${netErr.message || netErr})`);
      }
      const raw = await res.text().catch(() => '');
      // No-content responses: nothing to parse (prevents "Unexpected end of JSON input")
      if (res.status === 204 || res.status === 205 || res.status === 304) return null;
      if (!res.ok) {
        let detail = raw;
        try { const j = JSON.parse(raw); detail = j.detail || j.message || raw; } catch {}
        throw new Error(`${res.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
      }
      if (!raw) return null;
      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        try { return JSON.parse(raw); } catch { return raw; }
      }
      return raw;
    },

    createSession: (title, topic) =>
      window.COM.api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify({ title, topic })
      }),

    listSessions: (offset = 0, limit = 50) =>
      window.COM.api(`/api/sessions?offset=${offset}&limit=${limit}`),

    getSession: (id) => window.COM.api(`/api/sessions/${id}`),

    archiveSession: (id) =>
      window.COM.api(`/api/sessions/${id}`, { method: 'DELETE' }),

    listDeliberations: (sessionId) =>
      window.COM.api(`/api/sessions/${sessionId}/deliberations`),

    startDeliberation: (sessionId, query) =>
      window.COM.api(`/api/sessions/${sessionId}/deliberate`, {
        method: 'POST',
        body: JSON.stringify({ query })
      }),

    getDeliberation: (id) => window.COM.api(`/api/deliberations/${id}`),

    getDeliberationStatus: (id) => window.COM.api(`/api/deliberations/${id}/status`),

    submitVerdict: (deliberationId, data) =>
      window.COM.api('/api/verdicts', {
        method: 'POST',
        body: JSON.stringify({ deliberation_id: deliberationId, ...data })
      }),

    getVerdict: (deliberationId) =>
      window.COM.api(`/api/verdicts/${deliberationId}`),

    uploadDocument: async (filePaths) => {
      const formData = new FormData();
      for (const fp of filePaths) {
        const resp = await fetch(fp);
        const blob = await resp.blob();
        const name = fp.split('/').pop() || fp.split('\\').pop();
        formData.append('file', blob, name);
      }
      return fetch(`${window.COM_API_BASE}/api/documents/upload`, {
        method: 'POST',
        body: formData
      }).then(r => r.ok ? r.json() : Promise.reject(r));
    },

    searchDocuments: (query) =>
      window.COM.api(`/api/documents/search?q=${encodeURIComponent(query)}`),

    listDocuments: () => window.COM.api('/api/documents'),

    getDocument: (id) => window.COM.api(`/api/documents/${id}`),

    deleteDocument: (id) =>
      window.COM.api(`/api/documents/${id}`, { method: 'DELETE' }),

    saveScoutReport: (data) =>
      window.COM.api('/api/documents/save-scout-report', {
        method: 'POST',
        body: JSON.stringify(data)
      }),

    getAgents: () => window.COM.api('/api/agents'),

    getAgentStats: (name) => window.COM.api(`/api/agents/${name}/stats`),

    health: () => window.COM.api('/api/health'),

    isReady: () => window.COM.api('/api/health/ready'),

    getProviders: () => window.COM.api('/api/providers'),

    switchProvider: (provider) =>
      window.COM.api('/api/providers/switch', {
        method: 'POST',
        body: JSON.stringify({ provider }),
      }),

    divination: (query, provider = 'glm') =>
      fetch(`${window.COM_API_BASE}/api/divination/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, provider }),
      }).then(r => r.ok ? r.json() : Promise.reject(r)),

    saveDivination: (data) =>
      window.COM.api('/api/divination/save', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  };
})();
