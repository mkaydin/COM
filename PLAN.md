# COM — Council of Minds: Desktop Application Plan

## Project Vision

Transform the **Council of Minds** web UI into a full-stack desktop application — a multi-agent deliberation system where specialized AI agents with distinct cognitive architectures debate, analyze, and synthesize responses to user queries. The user remains the final authority, assisted by a personal Companion AI.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                 Electron Desktop Shell                    │
│  ┌───────────────────────────────────────────────────┐  │
│  │              Web UI (HTML/CSS/JS)                  │  │
│  │    Left Sidebar  │  Main Canvas  │  Mobile Nav     │  │
│  └───────────────────────────────────────────────────┘  │
│                         │ IPC                             │
│  ┌───────────────────────────────────────────────────┐  │
│  │           Electron Main Process (Node.js)           │  │
│  │  File I/O  │  Native Menus  │  Python Proc Mgmt   │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │ HTTP/WS
          spawns & manages  │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Python Backend (ALL logic)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ REST API │ │ WebSocket│ │  Task    │ │  Auth       │ │
│  │  Server  │ │  Gateway │ │  Queue   │ │  Middleware │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
│                           │                               │
│  ┌────────────────────────────────────────────────────┐ │
│  │              LangGraph Agent System                  │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐ │ │
│  │  │  Scout   │ │ Historian│ │Detective │ │ Sage  │ │ │
│  │  │(Web Intel│ │  (RAG)   │ │(GraphRAG)│ │(Wiki) │ │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └───────┘ │ │
│  │  ┌──────────┐ ┌──────────────────────┐            │ │
│  │  │ Moderator│ │   Companion AI       │            │ │
│  │  │ (Arbiter)│ │   (User Assistant)   │            │ │
│  │  └──────────┘ └──────────────────────┘            │ │
│  └────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Data Layer (Python-managed)             │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │ │
│  │  │PostgreSQL│ │pgvector  │ │ ChromaDB         │   │ │
│  │  └──────────┘ └──────────┘ └──────────────────┘   │ │
│  │  ┌──────────┐ ┌──────────┐                          │ │
│  │  │  Neo4j   │ │  Redis   │                          │ │
│  │  └──────────┘ └──────────┘                          │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack

### Desktop Shell
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Shell | **Electron 33+** | Chromium-based, stable on Linux/Wayland via `--ozone-platform=wayland`, mature ecosystem |
| UI | **Existing HTML + Tailwind CSS + Vanilla JS** | Already built, pixel-art dark fantasy theme, zero changes needed |
| IPC | **contextBridge + ipcRenderer** (Node.js ↔ JS bridge) | File dialogs, native menus, notifications, Python process lifecycle |
| State | **electron-store** | Persistent local preferences, session cache, LLM key storage (encrypted) |
| Build | **electron-builder** | Cross-platform packaging: AppImage/deb/rpm (Linux), dmg/pkg (macOS), NSIS/msi (Windows) |

### Scout Scraper Runtime (Node.js subprocess)
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Browser | **Lightpanda** | Headless browser, renders JS, ~50MB (vs ~300MB Chromium) |
| Driver | **Puppeteer** (puppeteer-core) | Connects to Lightpanda via WebSocket, extracts rendered HTML |
| Integration | **Node.js subprocess** | Python calls `node scout_scraper.mjs <url>`, captures HTML from stdout |
| Parsing | **BeautifulSoup4** (Python) | Strips noise, extracts clean text from rendered HTML |

### Python Backend (ALL logic)
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Framework | **FastAPI** (Python 3.12+) | Async-native, WebSocket support, auto OpenAPI docs |
| Agent Framework | **LangGraph** | Stateful multi-agent orchestration, graph-based workflows, checkpointer |
| LLM Provider | **LiteLLM** (abstraction layer) | Provider-agnostic — OpenAI, Anthropic, Ollama (local), 100+ models |
| Task Queue | **Celery + Redis** | Background deliberation jobs, automatic retry, rate limiting |
| Embeddings | **sentence-transformers / OpenAI** | Document and query embedding |
| Data Access | **SQLAlchemy 2.0 + Alembic** | PostgreSQL ORM, async sessions, migration management |
| Graph Access | **neo4j Python driver** | Cypher queries, graph traversal for Detective agent |
| Vector Access | **chromadb-client + psycopg2** | Document vector storage, pgvector queries |

### Managed Services (all via Docker, Python-driven)
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Relational DB | **PostgreSQL 16** | Sessions, users, deliberation history, agent state, pgvector |
| Vector Index | **pgvector** (PostgreSQL extension) | Co-located with relational data, single query surface |
| Document Store | **ChromaDB** | Lightweight Python-native embeddings, per-agent collection partitioning |
| Graph Store | **Neo4j Community** | GraphRAG engine for the Detective agent, Cypher query language |
| Cache/Broker | **Redis 7** | Celery broker, WebSocket pub/sub, session state cache |

### Sage's LLM Wiki Storage (filesystem, git-backed)
| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Wiki directory | **Markdown files** in `wiki/` | Human-readable, Obsidian-compatible, git-tracked, LLM-native |
| Raw sources | **Files in `raw/`** | Immutable originals (PDF, MD, images), read by Sage but never modified |
| Schema | **`SCHEMA.md`** | Conventions + workflows for the Sage as a wiki maintainer |
| Index | **`wiki/index.md`** | Catalog of all wiki pages with links + summaries, updated on every ingest |
| Log | **`wiki/log.md`** | Append-only chronological record of ingests, queries, lint passes |
| Search (optional) | **qmd** (CLI) | Local hybrid BM25/vector search over markdown files when wiki outgrows index.md |
| Sync/backup | **git** | Wiki is a git repo — version history, branching, rollback for free |

---

## 3. Agent System Design (LangGraph)

### 3.1 Agent Graph Topology

```
                      ┌──────────────┐
                      │  User Input  │
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │ Task Router  │  ← Classifies query type
                      │   (Router)   │
                      └──────┬───────┘
                             │
    ┌────────────────────────┼────────────────────────┐
    │                        │                        │
┌───▼───────┐ ┌──────▼─────┐ ┌──────▼─────┐ ┌──────▼──────┐
│   Scout   │ │  Historian │ │ Detective  │ │    Sage     │
│ (Web Intel│ │  (RAG Node)│ │(GraphRAG)  │ │ (LLM Wiki)  │
│  Scraping)│ │            │ │            │ │             │
└───┬───────┘ └──────┬─────┘ └──────┬─────┘ └──────┬──────┘
    │                 │              │              │
    └─────────────────┼──────────────┼──────────────┘
                      │              │
              ┌───────▼──────────────▼───────┐
              │         Moderator            │  ← Synthesizes all 4
              │         (Arbiter)            │     flags conflicts
              └─────────────┬───────────────┘
                            │
                   ┌────────▼────────┐
                   │  Cross-Critique │  ← Optional: agents review each other
                   │     (Loop)      │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Companion AI    │  ← Translates, clarifies for user
                   │ (User Assistant)│
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │   User Verdict  │  ← User decides / edits / overrides
                   │   (Final Node)  │
                   └─────────────────┘
```

### 3.2 Agent Definitions

#### Scout (`scout_agent.py`)
- **LangGraph Node**: `ScoutNode` — runs in parallel with the other three council members
- **Core Identity**: Exploratory intelligence operative. Not a search engine wrapper — a reconnaissance agent that explores unknown information spaces, detects emerging signals, and gathers external intelligence.
- **Core Philosophy**: Truth is probabilistic and evolves over time. Sources conflict, narratives shift, trends emerge before facts stabilize. Scout tracks consensus, measures uncertainty, and distinguishes signal from noise.
- **Reasoning Style**: Concise, tactical, information-dense. Alert, adaptive, pragmatic, skeptical but curious. Tone like an OSINT researcher or intelligence analyst. Avoids emotional language.
- **Data Sources**: Web search, Wikipedia, GitHub issues, Hacker News, Reddit, forums, technical docs, changelogs, news, academic papers. Prioritizes recency, source diversity, and corroboration.
- **Web Pipeline** (Lightpanda + Puppeteer + BeautifulSoup):
  - **Lightpanda** headless browser renders JavaScript → produces final DOM/HTML
  - **Puppeteer** connects to Lightpanda via WebSocket, extracts rendered HTML
  - **BeautifulSoup** parses HTML efficiently — strips nav, infoboxes, refs, scripts
  - **LiteLLM** summarizes extracted text into structured intelligence
  - Pipeline: `node scout_scraper.mjs <url>` → HTML stdout → BeautifulSoup → LiteLLM
  - Wikipedia search uses lightweight `httpx` for URL resolution, then full Lightpanda for article rendering
  - For non-JS pages: optional `httpx` fallback bypasses Lightpanda for speed
- **Cognitive Layers** (5-stage pipeline):
  - **Layer 1 — Reconnaissance**: Search broadly across multiple sources
  - **Layer 2 — Filtering**: Remove noise, duplicates, low-trust content
  - **Layer 3 — Clustering**: Group similar findings together
  - **Layer 4 — Signal Extraction**: Identify important developments, trends, anomalies, conflicts
  - **Layer 5 — Tactical Summary**: Deliver compressed intelligence report
- **Special Capability — Weak Signal Tracking**:
  - Detects emerging technologies, early shifts, niche discussions, pre-consensus movements
  - Tracks consensus evolution (old vs recent, stale vs current)
  - Identifies sudden changes, unusual correlations, contradictory reporting
  - Recognizes weak signals becoming strong
- **Constraints**:
  - MUST separate facts from speculation
  - MUST indicate confidence level per finding
  - MUST mention uncertainty explicitly
  - MUST prefer multiple independent confirmations before reporting as consensus
  - MUST identify outdated information (temporal awareness)
  - MUST compress large information spaces into actionable summaries
  - MUST NOT present rumors as facts
  - MUST NOT over-trust a single source
  - MUST NOT fabricate consensus or hallucinate citations
  - MUST NOT become philosophical or deeply theorize causal structures (that's Detective/Sage)
- **LLM Prompt Parameters**:
  - `temperature=0.3` (low — must stay factual, not creative)
  - System prompt enforces tactical OSINT mindset, evidence separation, uncertainty marking
- **Output Format**: Structured `[SCOUT REPORT]` with Key Signals, Consensus, Conflicting Information, Source Reliability, Confidence score
- **Save to Library**: User can optionally save any Scout report (summarized + original texts + source URLs) to the council's document library via `POST /api/documents/save-scout-report`. Saved reports appear in the Runic Library under the "Scout Reports" filter. This is always user-initiated — Scout never auto-saves.

#### Historian (`historian_agent.py`)
- **LangGraph Node**: `HistorianNode`
- **Reasoning Style**: Evidence-bound, citation-heavy
- **Data Sources**: ChromaDB vector search, PostgreSQL document index
- **Constraints**:
  - Must cite at least one source per claim
  - Confidence < 0.6 must output "Insufficient Data"
  - Output format: Structured JSON with citations
- **LLM Prompt Parameters**:
  - `temperature=0.3` (low creativity)
  - System prompt enforces evidence-only reasoning

#### Detective (`detective_agent.py`)
- **LangGraph Node**: `DetectiveNode`
- **Reasoning Style**: Relationship-focused, causal chains
- **Data Sources**: Neo4j graph traversal, pgvector similarity
- **Constraints**:
  - Must output entity-relationship chains (A → B → C)
  - Must identify at least one hidden link
  - Explicitly flags inferred (vs. factual) connections
- **LLM Prompt Parameters**:
  - `temperature=0.6` (moderate creativity for pattern finding)
  - System prompt demands structural reasoning

#### Sage (`sage_agent.py`)
- **LangGraph Node**: `SageNode`
- **Reasoning Style**: Abstract synthesis, analogical thinking, accumulated wisdom
- **Data Sources**: LLM Wiki (persistent, compounding knowledge base) + light graph traversal + internal reasoning
- **Core Technique**: **LLM Wiki** — a living, structured markdown knowledge base that the Sage incrementally builds and maintains. Unlike RAG (retrieve → generate → forget), the Sage:
  - Ingests sources once, extracts key concepts, and **files them persistently** into the wiki
  - Maintains entity pages, concept pages, topic summaries, and cross-references across the wiki
  - Updates existing pages when new sources arrive — revising summaries, noting contradictions, strengthening or challenging the evolving synthesis
  - Reads the wiki at query time instead of re-deriving knowledge from raw documents — the cross-references are already there, the contradictions already flagged
  - Compounds knowledge over time: every query, every source, every lint pass enriches the wiki further
- **Wiki Architecture** (three layers):
  - **Raw sources** (`raw/`) — immutable source documents (papers, articles, book chapters, transcripts). The Sage reads from these but never modifies them.
  - **The wiki** (`wiki/`) — LLM-generated markdown files. Summaries, entity pages, concept pages, comparisons, an overview (`index.md`), an append-only chronological log (`log.md`). The Sage owns this layer entirely.
  - **The schema** (`SCHEMA.md`) — tells the Sage how the wiki is structured, what conventions to follow, what workflows to use (ingest, query, lint). Co-evolved over time between the user and the Sage.
- **Operations**:
  - **Ingest**: User drops a source → Sage reads it, discusses key takeaways, writes summary page, updates index + entity pages + concept pages, appends to log. A single source can touch 10-15 wiki pages.
  - **Query**: User asks a question → Sage searches wiki (`index.md` first, then relevant pages), synthesizes answer with citations from the wiki. Good answers are filed back as new pages.
  - **Lint**: Periodic health check — contradictions between pages, stale claims superseded by newer sources, orphan pages, missing cross-references, data gaps to fill.
- **Constraints**:
  - Must provide at least one cross-domain analogy drawing from wiki knowledge
  - Must cite specific wiki pages as sources (e.g. `wiki/concepts/temporal-distortion.md`)
  - Must consider ethical / philosophical dimensions when relevant
  - Must acknowledge when interpretation is speculative vs. wiki-documented fact
  - Must update the wiki after every deliberation — filing insights as new pages or updating existing ones
- **LLM Prompt Parameters**:
  - `temperature=0.7` (moderate-high: creative synthesis but grounded in stored knowledge)
  - System prompt includes the full SCHEMA.md, instructing the Sage to act as a disciplined wiki maintainer, not a generic chatbot

#### Moderator (`moderator_agent.py`)
- **LangGraph Node**: `ModeratorNode`
- **Role**: Hidden arbiter, does NOT decide
- **Input**: All four agent responses (Scout, Historian, Detective, Sage)
- **Output**:
  - Structured comparison matrix
  - Agreement / conflict / uncertainty flags
  - Decision space mapped (Conservative / Exploratory / Strategic)
- **LLM Prompt Parameters**:
  - `temperature=0.2` (very structured output)

#### Companion AI (`companion_agent.py`)
- **LangGraph Node**: `CompanionNode`
- **Role**: User's personal advisor
- **Functions**:
  - Translates council responses into plain language
  - Suggests which agent to trust (contextual)
  - Asks clarifying follow-ups on user's behalf
  - Explains tradeoffs in non-technical terms

### 3.3 Cross-Critique Round (Optional Loop)

After the Moderator identifies conflicts, agents can respond to each other:

```
State: {
  "critique_round": 0,
  "max_rounds": 2,
  "conflicts": [
    {"agents": ["detective", "historian"], "topic": "causal_claim_x", "status": "unresolved"}
  ]
}
```

- Scout cross-validates source claims against each other
- Historian responds to Detective's inferences
- Detective challenges Historian's source quality
- Sage identifies the broader pattern others miss
- Stops at `max_rounds` or when conflicts resolve
- Scout can also be critiqued: other agents may challenge source recency/reliability

---

## 4. Database Schema

### 4.1 PostgreSQL Core Tables

```sql
-- Users & Sessions
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    title TEXT,
    topic TEXT,
    status TEXT DEFAULT 'active',  -- active, archived, deleted
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Deliberation Records
CREATE TABLE deliberations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    round INTEGER NOT NULL DEFAULT 1,  -- 1=hypothesis, 2=debate, 3=resolution
    user_query TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending, running, completed, failed
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Agent Responses
CREATE TABLE agent_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deliberation_id UUID REFERENCES deliberations(id),
    agent_name TEXT NOT NULL,  -- scout, historian, detective, sage, moderator, companion
    response_json JSONB NOT NULL,  -- full structured output
    confidence FLOAT,
    reasoning_type TEXT,  -- evidence, inference, intuition, synthesis
    latency_ms INTEGER,
    token_count INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Cross-Critique Records
CREATE TABLE critiques (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deliberation_id UUID REFERENCES deliberations(id),
    critique_round INTEGER NOT NULL,
    source_agent TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    critique_text TEXT NOT NULL,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- User Verdicts
CREATE TABLE verdicts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deliberation_id UUID UNIQUE REFERENCES deliberations(id),
    chosen_response_id UUID REFERENCES agent_responses(id),
    user_override TEXT,  -- user's own answer if they override
    verdict_type TEXT,   -- accepted, overridden, combined
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Documents (for RAG)
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT,  -- wiki, paper, user_upload
    file_path TEXT,
    metadata_json JSONB,
    embedding vector(1536),  -- pgvector column
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_documents_embedding ON documents USING ivfflat (embedding vector_cosine_ops);
```

### 4.2 ChromaDB Collections

```
collections:
  - documents_shared       # Shared knowledge base (all agents)
  - documents_historian    # Historian's private memory (future)
  - session_contexts       # Per-session chat history embeddings
```

### 4.3 Neo4j Graph Schema

```
Node Labels:
  - Entity {name, type, source_doc}
  - Concept {name, domain, confidence}
  - Source {title, url, credibility_score}

Relationship Types:
  - RELATES_TO {weight, evidence_strength, agent_discovered}
  - CAUSES {confidence, chain_position}
  - CITES {relevance_score}
  - CONTRADICTS {confidence}
```

---

## 5. API Design (FastAPI)

### 5.1 REST Endpoints

```
POST   /api/sessions                    # Create new session
GET    /api/sessions                    # List sessions
GET    /api/sessions/{id}               # Get session details
DELETE /api/sessions/{id}               # Archive session

POST   /api/sessions/{id}/deliberate    # Start deliberation (async)
GET    /api/sessions/{id}/deliberations # List deliberations in session
GET    /api/deliberations/{id}          # Get deliberation with all responses
GET    /api/deliberations/{id}/status   # Poll deliberation status

POST   /api/documents/upload            # Upload document for RAG
POST   /api/documents/save-scout-report  # Save Scout web intel to library (user-initiated)
GET    /api/documents/search            # Search documents
DELETE /api/documents/{id}              # Remove document

POST   /api/verdicts                    # Submit user verdict
GET    /api/verdicts/{deliberation_id}  # Get verdict for deliberation

GET    /api/agents                      # List available agents + status
GET    /api/agents/{name}/stats         # Agent performance stats
```

### 5.2 WebSocket Endpoints

```
WS /ws/sessions/{id}/stream            # Real-time deliberation streaming
  → Client sends: {"type": "query", "content": "..."}
  ← Server sends: {
      "type": "agent_update",
      "agent": "scout", "historian", "detective", "sage",
      "status": "thinking" | "searching" | "scraping" | "summarizing" | "responding" | "done",
      "partial": "..." | null,
      "confidence": 0.85,
      "sources": [...]
    }
  ← Server sends: {"type": "moderator_summary", "content": {...}}
  ← Server sends: {"type": "companion_note", "content": "..."}
  ← Server sends: {"type": "deliberation_complete", "deliberation_id": "..."}
```

---

## 6. Frontend Integration (Desktop App)

### 6.1 Electron Setup

```js
// desktop/main.js — Electron main process
const { app, BrowserWindow, dialog, Menu, ipcMain, Tray, Notification } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const Store = require('electron-store');

const store = new Store({ encryptionKey: 'com-council-key' });

let mainWindow;
let pythonProcess;

function startPythonBackend() {
  const pythonPath = store.get('pythonPath') || 'python3';
  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..', 'backend');

  pythonProcess = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--port', '8734'], {
    cwd: backendDir,
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });

  pythonProcess.stderr.on('data', (data) => console.log(`[backend] ${data}`));
  pythonProcess.on('close', (code) => console.log(`[backend] exited: ${code}`));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1024, minHeight: 700,
    title: 'COM — Council of Minds',
    backgroundColor: '#1a1a2e',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (process.env.NODE_ENV === 'dev') {
    mainWindow.loadURL('http://localhost:5500/council_landing_page.html');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'com_website', 'council_landing_page.html'));
  }

  mainWindow.on('closed', () => { mainWindow = null; });
}

// IPC handlers — only thin shell operations
ipcMain.handle('dialog:openFile', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Documents', extensions: ['pdf', 'txt', 'md', 'csv', 'json'] }]
  });
  return result.filePaths;
});

ipcMain.handle('dialog:saveFile', async (_, defaultName) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName,
    filters: [{ name: 'PDF', extensions: ['pdf'] }]
  });
  return result.filePath;
});

ipcMain.handle('store:get', (_, key) => store.get(key));
ipcMain.handle('store:set', (_, key, value) => store.set(key, value));

ipcMain.handle('notification:show', (_, { title, body }) => {
  new Notification({ title, body }).show();
});

ipcMain.handle('backend:getPort', () => 8734);
ipcMain.handle('backend:isReady', async () => {
  try {
    const res = await fetch('http://localhost:8734/api/health');
    return res.ok;
  } catch { return false; }
});

app.whenReady().then(() => {
  startPythonBackend();
  createWindow();
});

app.on('window-all-closed', () => {
  if (pythonProcess) pythonProcess.kill();
  app.quit();
});
```

```js
// desktop/preload.js — secure bridge to renderer
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  openFileDialog: () => ipcRenderer.invoke('dialog:openFile'),
  saveFileDialog: (name) => ipcRenderer.invoke('dialog:saveFile', name),
  storeGet: (key) => ipcRenderer.invoke('store:get', key),
  storeSet: (key, value) => ipcRenderer.invoke('store:set', key, value),
  showNotification: (opts) => ipcRenderer.invoke('notification:show', opts),
  getBackendPort: () => ipcRenderer.invoke('backend:getPort'),
  isBackendReady: () => ipcRenderer.invoke('backend:isReady')
});
```

```json
// desktop/package.json
{
  "name": "com-council-of-minds",
  "version": "1.0.0",
  "description": "Multi-agent AI deliberation system",
  "main": "main.js",
  "scripts": {
    "start": "NODE_ENV=dev electron .",
    "build:linux": "electron-builder --linux",
    "build:mac": "electron-builder --mac",
    "build:win": "electron-builder --win"
  },
  "build": {
    "appId": "ai.council.minds",
    "productName": "Council of Minds",
    "files": ["main.js", "preload.js", "../com_website/**/*"],
    "extraResources": [{ "from": "../backend", "to": "backend" }],
    "linux": {
      "target": ["AppImage", "deb", "rpm"],
      "category": "Office",
      "executableArgs": ["--ozone-platform=wayland"]
    },
    "mac": { "target": ["dmg", "pkg"] },
    "win": { "target": ["nsis", "msi"] }
  },
  "devDependencies": {
    "electron": "^33.0.0",
    "electron-builder": "^25.0.0"
  },
  "dependencies": {
    "electron-store": "^10.0.0"
  }
}
```

### 6.2 Frontend Page Updates Needed

| Page | Current State | Desktop App Changes |
|------|--------------|-------------------|
| `council_landing_page.html` | Static hero + agent cards | Connect "Enter Council" to WebSocket session |
| `council_chamber.html` | Static deliberation view | Wire textarea to backend, render live agent responses, streaming partials |
| `history.html` | Static demo cards | Fetch from PostgreSQL via API, real pagination |
| `runic_library.html` | Static upload area | Functional file upload → document ingestion pipeline |
| `council_rules.html` | Static rules | No change (informational page) |
| `agent_profiles.html` | Static profiles | Fetch agent configs + runtime stats from `/api/agents` |
| `deliberation_view.html` | Static transcript | Fetch full deliberation from `/api/deliberations/{id}` |

### 6.3 Native Features (Electron Main Process)

```
ipcMain handlers:
  - dialog:openFile           → native file picker for document upload
  - dialog:saveFile           → native save dialog for PDF export
  - store:get / store:set     → encrypted local preferences (electron-store)
  - notification:show         → OS-level desktop notifications
  - backend:getPort           → dynamic port discovery for Python process
  - backend:isReady           → health check before UI loads
  - spawn_backend()           → start/stop Python uvicorn process
  - app:getPlatform           → detect Linux/macOS/Win for platform-specific behavior
```

The Electron main process is a **thin shell only**. All business logic, agent orchestration, database access, and data processing lives in the Python backend.

---

## 7. Data Ingestion Pipeline

Four ingestion paths — one per council agent, each using a distinct strategy:

### 7.1 Historian Pipeline: Vector RAG

```
User Upload (PDF/TXT/MD/URL)
       │
       ▼
  ┌──────────────┐
  │  File Parser  │  ← LangChain Document Loaders
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │  Text Splitter│  ← RecursiveCharacterTextSplitter (chunk_size=1000)
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │  Embedding    │  ← OpenAI text-embedding-3-small / bge-large-en
  └──────┬───────┘
         │
    ┌────┴────┐
    ▼         ▼
┌──────┐ ┌──────────┐
│pgvec.│ │ ChromaDB │  ← pgvector for relational queries, ChromaDB for doc collections
└──────┘ └──────────┘

Query time: user question → embed → vector similarity search → retrieve top-k chunks → cite sources
```

### 7.2 Detective Pipeline: GraphRAG

```
User Upload (PDF/TXT/MD/URL)
       │
       ▼
  ┌──────────────┐
  │  File Parser  │  ← LangChain Document Loaders (shared parser)
  └──────┬───────┘
         │
  ┌──────▼───────────────┐
  │  Entity Extraction    │  ← LLM extracts named entities + concepts from full text
  └──────┬───────────────┘
         │
  ┌──────▼───────────────┐
  │  Relationship         │  ← LLM identifies relationships between extracted entities
  │  Extraction           │     (causal, structural, correlational, contradictory)
  └──────┬───────────────┘
         │
  ┌──────▼───────┐
  │  Neo4j Insert │  ← CREATE (n:Entity {name, type})-[r:RELATES_TO {weight}]→(m:Entity)
  └──────────────┘

Query time: user question → entity extraction → Cypher traversal (A→B→C chains) → build causal graphs
```

### 7.3 Key Differences

| Aspect | Historian (Vector RAG) | Detective (GraphRAG) |
|--------|----------------------|---------------------|
| Stores | Document chunks with embeddings | Entities + relationships as graph |
| Retrieval | Semantic similarity (cosine) | Graph traversal (Cypher) |
| Output | Cited passages from sources | Causal chains, connection maps |
| Strength | "What does the evidence say?" | "How are things connected?" |
| Weakness | Misses indirect relationships | Can overfit patterns in sparse data |

### 7.4 Scout Pipeline: Lightpanda + BeautifulSoup + LiteLLM

```
User Query (keywords)
       │
       ▼
  ┌──────────────┐
  │ Wikipedia     │  ← httpx: resolve article URL from search
  │ URL Resolver  │
  └──────┬───────┘
         │
  ┌──────▼──────────────────────────┐
  │ Lightpanda Headless Browser     │  ← Renders JavaScript, produces final DOM
  │ (Node.js subprocess)            │     scout_scraper.mjs <url> → stdout
  └──────┬──────────────────────────┘
         │  rendered HTML
  ┌──────▼───────┐
  │ BeautifulSoup │  ← Strip nav, infoboxes, scripts, refs, footers
  │   (Python)    │     Extract p, h1-h3, li, blockquote, pre, code
  └──────┬───────┘
         │  clean text (capped at 8000 chars)
  ┌──────▼───────┐
  │   LiteLLM    │  ← Summarize into structured intelligence
  │ Summarization│     Extract signals, consensus, contradictions
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │ Scout Report  │  ← Structured JSON: key_signals, consensus, conflicts,
  │   (JSON)      │     temporal_notes, sources, confidence
  └──────────────┘

Why Lightpanda (not just httpx):
- JavaScript-rendered pages (SPAs, dynamic content) need a real browser
- Lightpanda is lighter than full Chromium (~50MB vs ~300MB)
- BeautifulSoup then parses the rendered HTML efficiently (no browser needed for parsing)
- For static pages: optional httpx fallback bypasses Lightpanda for speed
```

### 7.5 LLM Wiki Pipeline (Sage)

This is fundamentally different from RAG. The Sage doesn't just chunk and embed documents — it **reads, understands, and integrates** them into the persistent wiki.

```
User drops source (PDF/MD/URL) into raw/
       │
       ▼
  ┌──────────────────┐
  │ Sage reads source │  ← Full document, not chunked
  │ + discusses with  │     User can guide emphasis and interpretation
  │      user         │
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Writes summary    │  ← wiki/sources/<title>.md
  │ page              │
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Updates entity    │  ← Creates or updates wiki/entities/<name>.md
  │ pages             │     for each key entity found
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Updates concept   │  ← Creates or updates wiki/concepts/<topic>.md
  │ pages             │     cross-referencing related pages
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Checks for        │  ← Reads related pages, flags contradictions,
  │ contradictions    │     notes where new data challenges old claims
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Updates index.md  │  ← Adds entry with link + one-line summary
  └──────┬───────────┘
         │
  ┌──────▼───────────┐
  │ Appends to log.md │  ← `## [2026-05-21] ingest | Article Title`
  └──────────────────┘

A single source can touch 10-15 wiki pages.
Every deliberation's insights can also be filed back as wiki pages.
The wiki compounds — it gets richer with every source and every query.
```

---

## 8. Project Structure

```
COM/
├── com_website/               # Existing HTML/CSS (Electron renderer)
│   ├── council_landing_page.html
│   ├── council_chamber.html
│   ├── history.html
│   ├── runic_library.html
│   ├── council_rules.html
│   ├── agent_profiles.html
│   ├── deliberation_view.html
│   └── styles.css
│
├── desktop/                    # Electron shell (thin: only window + IPC + Python mgmt)
│   ├── main.js                 # Electron main process
│   ├── preload.js              # contextBridge (secure IPC)
│   ├── package.json            # Electron deps + build config
│   └── icons/                  # App icons (png, icns, ico)
│
├── backend/                    # Python — ALL logic lives here
│   ├── pyproject.toml
│   ├── main.py                 # FastAPI app entry, lifespan (startup/shutdown)
│   ├── config.py               # Settings via pydantic-settings (env vars)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── sessions.py         # Session CRUD routes
│   │   ├── deliberations.py    # Deliberation trigger + status routes
│   │   ├── documents.py        # Document upload/search/delete routes
│   │   ├── verdicts.py         # User verdict submission routes
│   │   └── websocket.py        # WebSocket handler: real-time agent streaming
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── graph.py            # LangGraph workflow (StateGraph compile)
│   │   ├── scout.py            # Scout node: web search + scraping + signal extraction
│   │   ├── scout_scraper.mjs    # Lightpanda+Puppeteer headless scraper (Node.js)
│   │   ├── scout_websearch.py   # Python orchestrator: subprocess → BeautifulSoup → LiteLLM
│   │   ├── historian.py        # Historian node: RAG retrieval + evidence reasoning
│   │   ├── detective.py        # Detective node: GraphRAG + causal chain building
│   │   ├── sage.py             # Sage node: LLM Wiki — persistent compounding knowledge base
│   │   ├── sage_wiki.py        # Sage wiki engine: ingest, query, lint, index maintenance
│   │   ├── moderator.py        # Moderator node: comparison matrix + conflict detection
│   │   ├── companion.py        # Companion node: plain-language translation for user
│   │   ├── router.py           # Task router: classifies query type
│   │   ├── prompts.py          # System prompts per agent (temperature, constraints)
│   │   └── schemas.py          # Pydantic v2 output schemas with strict validation
│   ├── db/
│   │   ├── __init__.py
│   │   ├── postgres.py         # SQLAlchemy async engine + pgvector helpers
│   │   ├── chroma.py           # ChromaDB client (per-agent collections)
│   │   ├── neo4j.py            # Neo4j driver (graph extraction + traversal)
│   │   ├── redis.py            # Redis client (Celery broker + pub/sub)
│   │   ├── migrations/         # Alembic migration scripts
│   │   └── models.py           # SQLAlchemy ORM models
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── parser.py           # LangChain doc loaders (PDF, TXT, MD, URL)
│   │   ├── embedder.py         # Embedding generation + pgvector bulk insert
│   │   └── graph_extractor.py  # GraphRAG pipeline: entity extraction → Neo4j
│   ├── tasks/
│   │   ├── __init__.py
│   │   └── celery_app.py       # Celery worker config + background task definitions
│   └── tests/
│       ├── test_agents.py      # Unit tests per agent node
│       ├── test_graph.py       # LangGraph workflow integration tests
│       └── test_api.py         # FastAPI endpoint tests (httpx)
│
├── docker/                     # All services as containers
│   ├── docker-compose.yml      # PostgreSQL + pgvector + Neo4j + Redis + ChromaDB
│   ├── postgres.Dockerfile     # PostgreSQL 16 with pgvector extension
│   ├── neo4j.Dockerfile        # Neo4j Community with APOC plugins
│   └── backend.Dockerfile      # Python backend (for non-Electron deployment)
│
├── sage_wiki/                   # Sage's LLM Wiki (git-tracked, Obsidian-compatible)
│   ├── SCHEMA.md                # Wiki conventions + workflows (Sage reads this on every session)
│   ├── raw/                     # Immutable source documents
│   │   ├── papers/
│   │   ├── articles/
│   │   └── assets/              # Downloaded images
│   ├── wiki/                    # LLM-generated + maintained markdown
│   │   ├── index.md             # Catalog of all pages (links + summaries, categorized)
│   │   ├── log.md               # Append-only chronological log (ingests, queries, lint)
│   │   ├── entities/            # Entity pages (people, organizations, objects)
│   │   ├── concepts/            # Concept pages (ideas, theories, patterns)
│   │   ├── sources/             # One summary page per ingested source
│   │   ├── comparisons/         # User-requested comparison pages
│   │   ├── syntheses/           # Evolving synthesis — the Sage's current thesis on big questions
│   │   └── queries/             # Good answers filed back as persistent pages
│   └── .git/                    # Full version history of the wiki
│
├── character_persona.md        # Agent persona design philosophy
├── PLAN.md                     # This file
└── README.md
```

---

## 9. Implementation Roadmap

### Phase 1: Foundation (Week 1–2)
- [ ] Initialize Electron project with existing HTML frontend
- [ ] Set up Python project with FastAPI scaffold + pyproject.toml + `.env.example`
- [ ] Docker Compose: PostgreSQL + pgvector + Neo4j + Redis + ChromaDB
- [ ] Database migrations (Alembic — all core tables from Section 4)
- [ ] Electron main process: window creation, Python child process spawn/kill
- [ ] preload.js context bridge (file dialogs, store, notifications, backend port)
- [ ] Backend startup sequence: Docker check → service health polling → start uvicorn → poll `/api/health`
- [ ] Backend health endpoint that reports LLM and DB connectivity status
- [ ] Error recovery: Python crash auto-restart with exponential backoff, "Reconnecting..." overlay in frontend
- [ ] Logging setup: structlog on Python side (RotatingFileHandler), rotating log on Electron side
- [ ] Shared frontend JS: `api.js` (REST client), `ws.js` (WebSocket client with reconnect)
- [ ] Basic REST API: sessions CRUD, documents CRUD (upload, list, search, save-scout-report, delete), `/api/health`
- [ ] Install Node.js deps for Scout scraper: `@lightpanda/browser` + `puppeteer-core` (`backend/agents/package.json`)
- [ ] `scout_scraper.mjs`: Lightpanda + Puppeteer headless scraper (renders JS → outputs HTML to stdout)

### Phase 2: Agent Core (Week 3–4)
- [ ] Pydantic output schemas with strict validation (all 6 agents from Section 11 + Section 19)
- [ ] LiteLLM integration for LLM provider (configurable via .env)
- [ ] System prompts per agent with different temperature settings (Section 3.2)
- [ ] Implement Router agent (query classification: factual/relational/strategic/general)
- [ ] Implement Scout agent:
  - `scout_websearch.py`: Python orchestrator — Wikipedia URL resolution → Lightpanda subprocess → BeautifulSoup → LiteLLM
  - `scout_scraper.mjs`: Lightpanda renders JS → Puppeteer extracts rendered HTML → stdout
  - Scout LangGraph node with Pydantic output schema, signal extraction, weak signal tracking
- [ ] Implement Historian agent (vector RAG retrieval + evidence-bound reasoning)
- [ ] Implement Detective agent (Neo4j graph traversal + causal chain building)
- [ ] Implement Sage agent (LLM Wiki engine: `sage_wiki.py` — ingest, query, lint)
- [ ] Implement Moderator agent (comparison matrix, conflict/agreement detection, decision space — 4 agents)
- [ ] Implement Companion AI (translation, trust guidance, clarifying questions, recommendation)
- [ ] LangGraph workflow: router → parallel 4 agents → moderator → companion (Section 13)
- [ ] Deliberation round state machine (pending → running → debating → resolving → completed)
- [ ] LangGraph checkpointing via PostgresSaver

### Phase 3: Streaming & UI (Week 5–6)
- [ ] WebSocket server: `/ws/sessions/{id}/stream` with per-agent streaming
- [ ] Wire `council_chamber.html` to full deliberation flow:
  - Page load → read session_id → connect WebSocket → render loading state
  - User submits query → `ws.sendQuery()` → render agent cards as they stream in
  - Agent status: thinking (spinner) → responding (streaming partials) → done (full card with confidence bar)
  - Moderator summary appears after all agents respond
  - Companion panel updates with trust guidance + clarifying questions
  - User verdict: click agent → submit verdict, or type override
- [ ] "New Inquiry" button flow: create new deliberation within session, reset UI
- [ ] Cross-critique loop: moderator detects conflicts → agents critique each other → re-synthesize
- [ ] Page navigation: session state passed via `sessionStorage` between multi-page navigations
- [ ] `council_landing_page.html`: "Enter Council" → create session → navigate to chamber

### Phase 4: Knowledge Layer (Week 7–8)
- [ ] Document upload: Electron native file dialog → `/api/documents/upload`
- [ ] Historian RAG pipeline (parse → chunk → embed → pgvector + ChromaDB)
- [ ] Detective GraphRAG pipeline (parse → entity extraction → relationship extraction → Neo4j)
- [ ] Scout web pipeline full integration:
  - Wikipedia search → Lightpanda render → BeautifulSoup extract → LiteLLM summarize
  - Multi-source scraping: Hacker News, GitHub issues, Reddit, forums, docs, news
  - Source reputation tracking (reliability, recency, bias per source)
  - Weak signal detection: track emerging topics across multiple scrapes over time
- [ ] Sage LLM Wiki engine full implementation:
  - Ingest: read source → discuss with user → write summary → update entity/concept pages → index + log
  - Query: index-first lookup → keyword search → selective deep read of 3-5 relevant pages → cite wiki pages
  - Lint: health-check wiki (contradictions, orphans, stale claims, data gaps)
  - Wiki cache: LRU memory cache invalidated on git change detection
- [ ] Sage wiki writing during deliberation: good answers filed back as new wiki pages
- [ ] Optional: `qmd` search integration when wiki exceeds ~200 pages
- [ ] `runic_library.html` ← functional document management UI (upload, list, search, delete, Scout Reports filter)

### Phase 5: History & Onboarding (Week 9)
- [ ] First-run setup wizard: LLM provider selection → API key input → model selection → test connection → proceed
- [ ] First-run checks: Node.js availability (for Lightpanda), Docker status, Python venv
- [ ] LLM key flow: Electron UI → `electron-store` (encrypted) → write backend `.env` → backend hot-reload
- [ ] `history.html` ← fetch sessions + deliberations from API, real pagination
- [ ] `deliberation_view.html` ← fetch full deliberation transcript with agent colors + verdict
- [ ] `agent_profiles.html` ← fetch agent configs + runtime stats from `/api/agents`
- [ ] PDF export: Moderator synthesis → Electron save dialog → write PDF
- [ ] Agent stats dashboard: per-agent average confidence, latency distribution, token usage, critique engagement rate

### Phase 6: Polish & Ship (Week 10)
- [ ] Error handling: LLM timeout → retry with backoff; rate limit → queue + notify; agent failure → show partial results + error state
- [ ] Error handling: Lightpanda subprocess timeout (45s cap), zombie process cleanup, Node.js not found fallback
- [ ] Loading states: skeleton cards during deliberation, spinner during ingestion, progress bar during wiki lint
- [ ] Loading states: Scout scraper progress (searching... → scraping... → summarizing...)
- [ ] WebSocket reconnection with exponential backoff + "Connection lost. Reconnecting..." banner
- [ ] System tray: minimize to tray, tray icon with quick actions (New Deliberation, Open Archive)
- [ ] OS notifications: deliberation complete, wiki lint results available
- [ ] Offline mode with local models (Ollama — switchable via config)
- [ ] Tests: agent unit tests, workflow integration tests, API endpoint tests, ingestion pipeline tests
- [ ] Installer builds: Linux AppImage/deb/rpm, macOS dmg/pkg, Windows NSIS/msi
- [ ] Performance profiling: LLM call latency, memory usage, wiki load time
- [ ] README with setup instructions, architecture diagram, agent descriptions

---

## 10. Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Agent framework | LangGraph | Stateful graph workflows, built for multi-agent, checkpointing |
| Desktop shell | **Electron** | Stable Chromium engine, native Wayland support, wraps existing HTML/CSS, no Rust toolchain needed |
| Backend language | **Python (exclusively)** | LangGraph, FastAPI, SQLAlchemy, ChromaDB — single language across all backend logic |
| Vector DB | pgvector + ChromaDB | pgvector for relational+vector queries; ChromaDB for doc collections |
| Graph DB | Neo4j Community | Industry standard for graph, Cypher query language |
| LLM abstraction | LiteLLM | Provider-agnostic, 100+ models, cost tracking |
| Async tasks | Celery + Redis | Proven for long-running ML tasks, retry support |
| Cross-critique | LangGraph conditional edges | Agents critique only when conflicts detected |
| Scout scraper | **Lightpanda + Puppeteer (Node.js) + BeautifulSoup (Python)** | Lightpanda renders JS (~50MB vs ~300MB Chromium), Puppeteer extracts HTML, BeautifulSoup parses — subprocess from Python |

---

## 11. JSON Output Schemas (Per Agent)

### Scout Response
```json
{
  "agent": "scout",
  "confidence": 0.74,
  "reasoning_type": "reconnaissance",
  "topic": "Local LLM inference trends",
  "key_signals": [
    {"signal": "Increased interest in multi-GPU consumer inference", "strength": "strong", "sources": 4},
    {"signal": "Rising adoption of quantized MoE models", "strength": "strong", "sources": 3},
    {"signal": "More experimentation with Rust-based inference runtimes", "strength": "emerging", "sources": 2}
  ],
  "consensus": "Medium-confidence shift toward distributed inference architectures",
  "conflicting_information": [
    {"topic": "MoE performance", "claim_a": "Benchmarks show significant gains", "claim_b": "Latency instability reported", "resolution": "unresolved"}
  ],
  "temporal_notes": [
    {"finding": "Most tutorials reference CUDA 12.2", "status": "outdated", "current": "CUDA 12.6 deployments increasing"}
  ],
  "source_diversity": "high",
  "source_reliability": "medium verification (community sources dominate)",
  "weak_signals": [
    "Small but increasing discussion clusters suggest renewed interest in local-first AI systems due to cloud cost concerns"
  ],
  "sources": [
    {"url": "https://example.com/thread", "type": "forum", "recency": "2026-05-20", "reliability": "medium"},
    {"url": "https://github.com/example/project", "type": "github", "recency": "2026-05-21", "reliability": "high"}
  ]
}
```

### Historian Response
```json
{
  "agent": "historian",
  "confidence": 0.85,
  "reasoning_type": "evidence",
  "claims": [
    {
      "text": "The artifact originated in the Northern Wastes",
      "sources": [
        {"doc_id": "uuid-1", "title": "Pre-Calamity Archives", "excerpt": "...", "relevance": 0.92}
      ]
    }
  ],
  "verdict": "The artifact is of pre-calamity design.",
  "uncertainties": ["Exact manufacturing date unknown"],
  "insufficient_data": false
}
```

### Detective Response
```json
{
  "agent": "detective",
  "confidence": 0.72,
  "reasoning_type": "inference",
  "causal_chains": [
    {
      "chain": ["Containment Matrix → Stasis Magic → Class-4 Phenomena"],
      "evidence_strength": 0.78,
      "is_inferred": true
    }
  ],
  "hidden_links": [
    {"entity_a": "Isotopic Signature", "entity_b": "Temporal Distortion", "relation": "correlates_with"}
  ],
  "inconsistencies": ["Sealing pattern atypical for Northern Wastes origin"],
  "verdict": "The artifact was deliberately sealed for containment, not storage."
}
```

### Sage Response
```json
{
  "agent": "sage",
  "confidence": 0.80,
  "reasoning_type": "intuition",
  "analogies": [
    {"domain": "Physics", "concept": "Observer effect in quantum mechanics", "application": "The artifact reflects intent, not inherent nature"}
  ],
  "synthesis": "The artifact is a mirror, not a weapon. Calamity depends on the wielder.",
  "ethical_dimensions": ["Power redistribution", "Future generations' autonomy"],
  "speculation_warning": true
}
```

### Moderator Synthesis
```json
{
  "agent": "moderator",
  "deliberation_id": "uuid-d",
  "comparison": {
    "scout": {"confidence": 0.74, "type": "reconnaissance", "strength": "Broad current awareness, source-diverse"},
    "historian": {"confidence": 0.85, "type": "evidence", "strength": "High verifiability"},
    "detective": {"confidence": 0.72, "type": "inference", "strength": "Strong structural logic"},
    "sage": {"confidence": 0.80, "type": "intuition", "strength": "Novel framing"}
  },
  "conflicts": [
    {"topic": "Artifact origin", "scout": "Multiple sources cite recent digs at Northern Wastes", "historian": "Northern Wastes", "detective": "Sealing pattern inconsistent with Northern Wastes", "status": "unresolved"}
  ],
  "agreements": [
    {"topic": "Artifact is dangerous", "agents": ["scout", "historian", "detective", "sage"]}
  ],
  "decision_space": [
    {"label": "Conservative", "action": "Follow Historian + Scout consensus", "risk": "low"},
    {"label": "Exploratory", "action": "Follow Detective's containment theory", "risk": "medium"},
    {"label": "Strategic", "action": "Follow Sage's intent-driven model", "risk": "medium"}
  ]
}
```

---

## 12. Agent Graph State (LangGraph TypedDict)

```python
from typing import TypedDict, List, Optional, Annotated
from langgraph.graph.message import add_messages

class CouncilState(TypedDict):
    # Input
    user_query: str
    session_id: str
    deliberation_id: str

    # Agent outputs
    scout_response: Optional[dict]
    historian_response: Optional[dict]
    detective_response: Optional[dict]
    sage_response: Optional[dict]
    moderator_synthesis: Optional[dict]
    companion_notes: Optional[str]

    # Critique loop
    critique_round: int
    max_critique_rounds: int
    unresolved_conflicts: List[dict]
    critiques: Annotated[List[dict], add_messages]

    # Flow control
    current_round: int          # 1=hypothesis, 2=debate, 3=resolution
    status: str                 # pending, running, completed, failed
    should_critique: bool       # whether cross-critique is needed

    # User
    user_verdict: Optional[dict]
    user_overridden: bool
```

---

## 13. LangGraph Workflow Pseudocode

```python
from langgraph.graph import StateGraph, END

workflow = StateGraph(CouncilState)

# Add nodes
workflow.add_node("router", task_router)
workflow.add_node("scout", scout_node)
workflow.add_node("historian", historian_node)
workflow.add_node("detective", detective_node)
workflow.add_node("sage", sage_node)
workflow.add_node("moderator", moderator_node)
workflow.add_node("companion", companion_node)
workflow.add_node("critique", critique_round_node)

# Edges
workflow.set_entry_point("router")
workflow.add_conditional_edges("router", route_to_agents, {
    "parallel": ["scout", "historian", "detective", "sage"]
})

# All agents converge to moderator
workflow.add_edge("scout", "moderator")
workflow.add_edge("historian", "moderator")
workflow.add_edge("detective", "moderator")
workflow.add_edge("sage", "moderator")

# Conditional: critique or proceed
workflow.add_conditional_edges("moderator", decide_critique, {
    "critique": "critique",
    "proceed": "companion"
})
workflow.add_conditional_edges("critique", decide_continue_critique, {
    "continue": "critique",
    "done": "companion"
})

workflow.add_edge("companion", END)

# Compile with checkpointing
app = workflow.compile(checkpointer=PostgresSaver)
```

---

## 14. Local / Offline Mode

For air-gapped or privacy-sensitive deployments:

| Component | Cloud | Local Alternative |
|-----------|-------|-------------------|
| LLM | OpenAI / Anthropic | Ollama (llama3, mistral, phi3) |
| Embeddings | OpenAI text-embedding-3 | sentence-transformers (bge-large-en) |
| GraphRAG | Neo4j Aura | Neo4j Community (Docker) |
| Vector DB | ChromaDB Cloud | ChromaDB local / pgvector |

Configuration via `.env`:
```env
LLM_PROVIDER=ollama       # ollama | openai | anthropic
LLM_MODEL=llama3:8b
EMBEDDING_MODEL=bge-large-en
GRAPH_DB_URL=bolt://localhost:7687
```

---

## 15. Frontend JS Layer (Missing from plan)

The current HTML pages are entirely static. They need a JavaScript layer to talk to the backend. This is the bridge between the Electron shell and the Python server.

### 15.1 Shared API Client (`com_website/api.js`)

```js
const API_BASE = `http://localhost:${window.electronAPI?.getBackendPort?.() || 8734}`;

async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Session helpers
window.COM = {
  createSession: (title) => api('/api/sessions', { method: 'POST', body: JSON.stringify({ title }) }),
  listSessions: () => api('/api/sessions'),
  getSession: (id) => api(`/api/sessions/${id}`),
  startDeliberation: (sessionId, query) => api(`/api/sessions/${sessionId}/deliberate`, { method: 'POST', body: JSON.stringify({ query }) }),
  getDeliberation: (id) => api(`/api/deliberations/${id}`),
  submitVerdict: (deliberationId, data) => api('/api/verdicts', { method: 'POST', body: JSON.stringify({ deliberation_id: deliberationId, ...data }) }),
  uploadDocument: async (filePaths) => { /* uses FormData + fetch */ },
  searchDocuments: (q) => api(`/api/documents/search?q=${encodeURIComponent(q)}`),
};
```

### 15.2 WebSocket Client (`com_website/ws.js`)

```js
window.COM.connectStream = function(sessionId, handlers) {
  const ws = new WebSocket(`ws://localhost:${API_BASE.split(':')[2]}/ws/sessions/${sessionId}/stream`);

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    switch (msg.type) {
      case 'agent_update':
        handlers.onAgentUpdate?.(msg.agent, msg.status, msg.partial, msg.confidence, msg.sources);
        break;
      case 'moderator_summary':
        handlers.onModeratorSummary?.(msg.content);
        break;
      case 'companion_note':
        handlers.onCompanionNote?.(msg.content);
        break;
      case 'deliberation_complete':
        handlers.onComplete?.(msg.deliberation_id);
        break;
      case 'error':
        handlers.onError?.(msg.message);
        break;
    }
  };

  ws.onclose = () => { /* auto-reconnect with exponential backoff */ };
  ws.sendQuery = (query) => ws.send(JSON.stringify({ type: 'query', content: query }));

  return ws;
};
```

### 15.3 Page Navigation Strategy

In Electron with local files, there's no URL routing. Two approaches:

| Approach | How it works | Tradeoff |
|----------|-------------|----------|
| **A: Multi-page** (chosen) | Each `.html` page loads in the same BrowserWindow via `window.location.href = 'history.html'`. State passed via `sessionStorage` or electron-store. | Simple, matches current site structure, no router needed |
| B: SPA | Single `index.html` with JS router (similar to React Router), dynamically swaps content | More complex, needs rewrite of all pages |

**Chosen: Approach A (multi-page).** When navigating, store the active session ID in `sessionStorage` so the next page can pick it up. Links in sidebars/mobile nav already use `href` attributes — they work naturally in Electron. The only addition: when landing pages load, they call the API to populate data.

### 15.4 Chamber Page Wiring (`council_chamber.html`)

The chamber page is the most JS-heavy. Flow:

```
Page loads
  ↓
Read session_id from sessionStorage (or create new session via API)
  ↓
Connect WebSocket to /ws/sessions/{id}/stream
  ↓
User types query → hits Submit → ws.sendQuery(query)
  ↓
┌─ onAgentUpdate("historian", "thinking") → show spinner
├─ onAgentUpdate("historian", "responding") → stream partial text
├─ onAgentUpdate("historian", "done") → render final card with confidence bar
├─ (same for detective, sage)
├─ onModeratorSummary(data) → render comparison + decision space
├─ onCompanionNote(text) → show in companion panel
└─ onComplete(deliberation_id) → enable verdict buttons
  ↓
User clicks an agent's verdict → submitVerdict(deliberation_id, { chosen_agent })
  OR user types own answer → submitVerdict(deliberation_id, { user_override: "..." })
```

### 15.5 History Page Wiring (`history.html`)

```
Page loads → COM.listSessions() → render session list
Click session → COM.getSession(id) → show deliberations within session
Click deliberation → COM.getDeliberation(id) → render full transcript
  OR navigate to deliberation_view.html?id={id}
```

---

## 16. First-Run / Onboarding Flow

When the app launches for the first time:

1. Electron main process starts Python backend
2. Backend checks if `~/.config/com/config.env` exists
3. If no config → backend returns `{"configured": false}` from `/api/health`
4. Electron shows **setup wizard** page:
   - LLM provider selection (OpenAI / Anthropic / Ollama)
   - API key input (masked, stored encrypted in electron-store)
   - Model selection dropdown
   - Status: "Checking services..." (Docker containers starting)
   - "Test Connection" button → sends a test prompt
5. On success → backend writes config, restarts if needed
6. Redirect to landing page

Configuration flow:
```
User enters key in Electron UI
  ↓
window.electronAPI.storeSet('llmKey', key)   ← encrypted store
  ↓
Electron main.js writes to backend .env file or passes via env var
  ↓
Backend restarts (or hot-reloads config)
  ↓
/api/health → {"configured": true}
```

---

## 17. Backend Startup & Service Dependencies

### Startup sequence

```
Electron starts
  ↓
1. Check if Docker is running
   ├─ Yes → docker compose up -d (PostgreSQL, Neo4j, Redis, ChromaDB)
   └─ No  → prompt user to install/start Docker
  ↓
2. Wait for services to be healthy (poll /api/health on each)
  ↓
3. Start Python backend (uvicorn)
  ↓
4. Poll http://localhost:8734/api/health until 200
  ↓
5. Load frontend page
```

### Error states during startup

| Failure | Behavior |
|---------|----------|
| Docker not installed | Show setup page with Docker install link |
| Port already in use | Try next port, update API_BASE in frontend |
| PostgreSQL fails | Retry 3x, show "Database connection failed" with logs |
| Python backend crashes | Auto-restart with exponential backoff (1s, 2s, 4s, max 30s); show "Reconnecting..." overlay |
| LLM API key invalid | `/api/health` returns `{"llm_ok": false}`; redirect to setup wizard |

### Docker Compose management

```yaml
# docker/docker-compose.yml — single command starts everything
services:
  postgres:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: council
      POSTGRES_USER: council
      POSTGRES_PASSWORD: council_dev
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  neo4j:
    image: neo4j:5-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/council_dev
    volumes: [neo4jdata:/data]

  chromadb:
    image: chromadb/chroma:latest
    ports: ["8000:8000"]
```

---

## 18. Deliberation Round System

The plan mentions 3 rounds but doesn't describe how they progress. Here's the full flow:

### Round 1: Hypothesis
- User submits initial query
- Agents produce first-pass responses (parallel)
- Moderator identifies conflicts, proposes decision space
- **User decides**: accept a single agent, ask for clarification, or advance to debate

### Round 2: Debate (cross-critique)
- If user wants deeper analysis or there are unresolved conflicts:
  - Each agent receives the other agents' responses
  - Agents critique and refine their positions
  - Moderator re-synthesizes
- **User decides**: accept, override, or advance to final round

### Round 3: Resolution
- Moderator produces final synthesis incorporating all critique rounds
- Companion translates and presents options
- **User delivers final verdict** — accept, override, or write their own

### State machine in the DB

```
deliberations.status:
  pending     → query submitted, not yet running
  running     → agents processing (round 1)
  debating    → cross-critique in progress (round 2)
  resolving   → final synthesis (round 3)
  completed   → verdict submitted
  failed      → error during processing
```

The LangGraph workflow naturally handles this with the `current_round` field in CouncilState. The `should_critique` boolean controls whether to enter the critique loop after moderator.

---

## 19. Companion AI Output Schema

```json
{
  "agent": "companion",
  "deliberation_id": "uuid-d",
  "translation": "In simple terms: the artifact is like a mirror that reflects the user's intentions. It's not inherently good or evil.",
  "trust_guidance": [
    {"agent": "scout", "trust_level": "medium", "reason": "Source-diverse current intel, but community-sourced — verify key claims"},
    {"agent": "historian", "trust_level": "high", "reason": "Claims are source-backed with high confidence"},
    {"agent": "detective", "trust_level": "medium", "reason": "Logical chains are strong but partially inferred"},
    {"agent": "sage", "trust_level": "medium", "reason": "Insightful framing but speculative — useful for strategy, less for facts"}
  ],
  "clarifying_questions": [
    "Would you like me to ask the Detective for more evidence on the sealing pattern?",
    "Should we explore what 'calamity' specifically means in this context?"
  ],
  "summary": "The council agrees the artifact is dangerous but disagrees on its origin. The safest path is to follow the Historian's evidence-backed caution. The most exploratory path is the Detective's containment theory.",
  "recommended_agent": "historian",
  "recommendation_reason": "When safety is at stake, evidence-backed claims should take priority over inference and intuition."
}
```

---

## 20. Router Agent Definition

```python
class QueryClassification(BaseModel):
    query_type: Literal[
        "factual",       # "What is X?" → prioritize Historian
        "relational",    # "How is X connected to Y?" → prioritize Detective
        "strategic",     # "What should we do about X?" → prioritize Sage
        "general"        # All agents equally
    ]
    confidence: float
    reasoning: str

def router_node(state: CouncilState) -> dict:
    """
    Classifies the user query and sets agent priority.
    All three agents always run in parallel, but the classification
    determines response ordering in the UI and which agent the
    Companion recommends by default.
    """
    # LLM call with specialized prompt
    classification = classify_query(state["user_query"])
    return {
        "query_type": classification.query_type,
        "agent_priority": get_priority_order(classification.query_type)
    }
```

The Router determines **presentation order** in the UI, not execution order — all agents still run in parallel. The priority helps the Companion decide which agent to recommend.

---

## 21. Sage Wiki Loading Strategy

The Sage reads from a git-tracked markdown directory. During deliberation, loading the entire wiki into context is impractical. Strategy:

1. **Index-first lookup**: Sage reads `wiki/index.md` (a catalog with 1-line summaries of every page)
2. **Keyword + tag search**: Extracts entities/concepts from the user query, searches `index.md` for matching pages
3. **Selective deep read**: Loads only the 3-5 most relevant wiki pages into the LLM context
4. **Full-page references**: Cites wiki page paths (e.g., `[[wiki/concepts/temporal-distortion]]`) so the user can open them in Obsidian/editor
5. **Cache in memory**: Frequently accessed pages are cached in `sage_wiki.py` (LRU cache, invalidated on git change detection)

When the wiki is small (< 50 pages), the Sage can load all of it. Beyond that, the index+search approach kicks in. Optional: integrate `qmd` (local hybrid BM25/vector search) when wiki exceeds ~200 pages.

---

## 22. Simplified Multi-User Model

The database has a `users` table but this is a desktop app. For v1:

- **Single local user** — auto-created on first launch with name from OS username
- `users` table exists for future multi-user support (e.g., team deployment), but v1 only has one row
- Sessions are all owned by the single local user
- No login/authentication — the desktop app is the security boundary

---

## 23. Testing Strategy

| Layer | Tool | What to test |
|-------|------|-------------|
| Agent nodes | `pytest` + mock LLM responses | Each agent produces valid JSON matching its Pydantic schema |
| LangGraph workflow | `pytest` + mock nodes | Graph edges correct, state flows properly through rounds |
| API endpoints | `httpx` + `pytest-asyncio` | REST routes return correct status codes and shapes |
| WebSocket | `httpx` async client | Streaming messages arrive in correct order |
| Frontend JS | Manual + future Playwright | UI rendering with mock backend data |
| Ingestion pipeline | `pytest` + test files | Parse → chunk → embed → store flows correctly |
| Sage wiki engine | `pytest` + temp directory | Ingest creates pages, query finds them, lint detects issues |

CI approach: `docker compose up -d` → wait for healthy → `pytest` → `docker compose down`.

---

## 24. Logging & Observability

```python
# backend uses structlog for structured logging
import structlog
logger = structlog.get_logger()

# Key log points:
logger.info("deliberation_started", session_id=sid, deliberation_id=did, query=query[:100])
logger.info("agent_response", agent="historian", latency_ms=1234, tokens=450, confidence=0.85)
logger.warning("agent_low_confidence", agent="detective", confidence=0.42)
logger.error("deliberation_failed", deliberation_id=did, error=str(e))
```

Electron side: `console.log` in main process, forwarded to a rotating log file (`~/.config/com/logs/`). Renderer errors caught via `window.onerror` and sent to main process for logging.

---

## 25. Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Agents produce same-tone responses | Strict prompt templates, different temperatures, different output schemas enforced via Pydantic validation |
| LangGraph complexity | Start with 3 parallel nodes + moderator, add critique loop later |
| Large binary size (Electron + Python) | Electron (~120MB) + embedded Python (~40MB via PyOxidizer) = ~160MB total; use electron-builder compression |
| Slow deliberation (3+ LLM calls) | Parallel agent calls via LangGraph, streaming WebSocket responses, progress indicators per agent |
| Vector DB sync issues | Use pgvector as source of truth, ChromaDB as read-optimized cache with TTL |
| Hallucinated citations | Validate document IDs against PostgreSQL at moderator node; mark unverifiable claims |
| Python process crashes | Electron monitors child process health; auto-restart with exponential backoff |
```

---

*Generated: 2026-05-21 | Next: Begin Phase 1 — Electron scaffold + FastAPI skeleton*
