(() => {
  const port = window.electronAPI?.getBackendPort?.() || 8734;
  window.COM_API_BASE = `http://localhost:${port}`;

  window.COM = {
    async api(path, opts = {}) {
      const url = `${window.COM_API_BASE}${path}`;
      const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
      }
      const contentType = res.headers.get('content-type') || '';
      return contentType.includes('application/json') ? res.json() : res.text();
    },

    createSession: (title, topic) =>
      window.COM.api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify({ title, topic })
      }),

    listSessions: () => window.COM.api('/api/sessions'),

    getSession: (id) => window.COM.api(`/api/sessions/${id}`),

    archiveSession: (id) =>
      window.COM.api(`/api/sessions/${id}`, { method: 'DELETE' }),

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
  };
})();
