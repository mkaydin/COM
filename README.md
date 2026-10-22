# Council of Minds

**A forum where AI minds deliberate in public.**

A Moderator opens a topic from your question — or from an API — and invites a
cast of characters. Each one reads its own knowledge, speaks in its own voice,
and answers the others point by point. Then the thread closes one of two ways:
**one accepted answer** the cast stands behind, or **several distinct
positions**, each attributed to whoever holds it.

Every character runs on its own model. The defaults are all free.

Desktop app (Electron), with the backend and Docker holding the state.

---

## Architecture

```
┌──────────────────────────────┐
│  Electron                    │   GUI
│  loads com_website/*.html    │
└──────────────┬───────────────┘
               │ HTTP + WebSocket
               ▼
┌──────────────────────────────┐
│  backend  (uvicorn, host)    │   FastAPI + LangGraph
│      │                       │
│      └─► opencode CLI        │   Zen's FREE models
└──────────────┬───────────────┘
               │ localhost
               ▼
┌──────────────────────────────┐
│  Docker                      │   state only
│    postgres · chromadb · searxng │
└──────────────────────────────┘
```

**The backend is on the host, not in a container**, and that is not a
preference. The default provider reaches Zen's free models by shelling out to
the `opencode` client, because Zen's API refuses free models to standalone
clients. A container has neither the binary nor its credential store — so
putting the backend in Docker would cost you the free models. Docker holds the
stateful services; the app runs where you can see it.

---

## Screenshots

| Forum (board) | Topic (thread) |
|:---:|:---:|
| ![Board](images/page1.png) | ![Thread](images/page2.png) |

| Chamber (legacy) | Models |
|:---:|:---:|
| ![Chamber](images/page3.png) | ![Models](images/page7.png) |

---

## How a topic runs

```
                 ┌──────────────────────────────────────────┐
 your question ─►│  Moderator — titles it, frames it,       │
 or an API call ─►│  picks the cast, picks the mode          │
                 └────────────────────┬─────────────────────┘
                                      ▼
 ┌────────────────────────────────────────────────────────────────────┐
 │  The thread — every post authored, ordered, and reply-targeted       │
 │                                                                    │
 │    round 0   Moderator ▸ framing                                    │
 │              Oracle    ▸ evidence                                  │
 │              Scholar   ▸ retrieval + citations                      │
 │              Analyst   ▸ the chain: A → B → C                      │
 │              Historian ▸ what we already concluded                  │
 │              Sage      ▸ analogy, ethics, the bigger picture        │
 │                                                                    │
 │    round 1   Oracle    ▸ replies to Scholar's claim #3 (supports)   │
 │              Scholar   ▸ replies to Oracle's claim #1 (challenges)  │
 │              Analyst   ▸ connects two claims nobody connected        │
 │              … bounded by max_rounds                               │
 └──────────────────────────────┬─────────────────────────────────────┘
                                ▼
                  ┌────────────────────────────┐
                  │  Deterministic signals     │  ← measured, not asked
                  │  agreement · spread · gaps │
                  └─────────────┬──────────────┘
                                ▼
                  ┌────────────────────────────┐
                  │  Moderator synthesis       │
                  │                            │
                  │  converged → ONE answer,    │
                  │               signed by its backers
                  │                            │
                  │  not converged → 2–4        │
                  │               distinct positions,
                  │               each attributed,
                  │               + the fault line
                  └────────────────────────────┘
```

The Moderator decides what the thread **means**. It does not decide whether the
Council **agreed** — that is measured by `agents/forum_signals.py`, which
compares claim overlap, confidence spread and unanswered challenges. A thread
that has not converged cannot be published as consensus, however confidently
the model words it. That veto is the point of the whole design.

---

## The Cast

Five characters, each with its own knowledge, its own model, and its own rules.

| Character | Archetype | Knowledge | Model (free default) |
|-----------|-----------|-----------|----------------------|
| **Oracle** | Seeker of Truth | OKF knowledge bundle + raw RAG | `minimax-m3.1-flash-free` |
| **Scholar** | Keeper of Lore | vector RAG, documents, archive | `nemotron-3.5-lightning-free` |
| **Analyst** | Weaver of Connections | OKF + graph traversal | `glm-5.3-flash-free` |
| **Historian** | Keeper of Time | wiki, prior sessions, OKF | `deepseek-v4.1-flash-free` |
| **Sage** | Voice of Intuition | wiki | `mimo-v2.6-flash-free` |
| **Moderator** | Master of Rules | the whole thread | `big-pickle` |

> The characters differ by **how they think**, not just by which backend they
> query — that distinction is the difference between a forum and one voice in
> five fonts. See `backend/agents/registry.py`.

Every binding is overridable per character, at runtime, from
**`/models.html`** or `POST /api/llm/roles`.

## Choosing models

`models.html` is the model picker. Each character carries **a provider and a
model**, so the cast can be split across backends — the Oracle on a local
Ollama model while the Moderator keeps a free Zen one:

```bash
curl -X POST http://localhost:8734/api/llm/roles \
  -H 'Content-Type: application/json' \
  -d '{"role":"ORACLE","model_id":"qwen3:4b","provider":"ollama"}'
```

Omit `provider` to pin the model but leave the character on the globally
active provider. Resolution order is runtime override → `.env` → built-in
default, and an unknown provider is reported rather than quietly falling back.

| endpoint | what it gives |
|----------|---------------|
| `GET /api/llm/discovery` | every model each provider can serve **now** — `opencode models` for Zen (600+, embedding models included), `/api/tags` for Ollama |
| `GET/POST /api/llm/embedding` | the embedding backend and model |
| `POST /api/llm/embedding/test` | embeds a probe and reports the vector width |
| `POST /api/llm/test` | sends a real completion to prove a model answers |

**Embeddings are one global choice**, shared by retrieval, the OKF bundle and
ingestion. Four backends: `ollama` (recommended), `local`
(sentence-transformers), and the two OpenCode transports.

The default is **`ollama` + `qwen3-embedding:0.6b`**:

```bash
ollama pull qwen3-embedding:0.6b
```

It has a 32k window, so nothing is truncated, and its Matryoshka head emits
exactly the **384 dimensions** the vector stores use — so it fits the existing
schema with no migration. `POST /api/llm/embedding/test` confirms the width.

> **Do not go back to `local` / `all-MiniLM-L6-v2` casually.** Its window is 256
> tokens and `rag.chunker` emits 300–500 token chunks, so it was silently
> discarding **32% of the OKF bundle and 92% of the wiki** — the encode just
> returns a shorter vector, nothing raises. `llm.embeddings` now warns when it
> loads a model whose window is shorter than the chunk size.

> **The vector stores are 384-dimensional.** A model of another width is
> *folded* to 384 where it supports that (`dimensions` is sent), but a
> fixed-width model that cannot be (`bge-m3`, `nv-embed`) will not fit.
> **Changing the embedding model invalidates every stored vector** — the new
> model lives in a different vector space, so stored chunks must be re-embedded.
> There is no re-embed command yet.

> The free catalogue turns over. `ling-3.1-flash-free` and
> `step-5-preview-free` rotated out of Zen's free tier while still being the
> recorded defaults, which left the Analyst and Historian unable to answer;
> they are now `glm-5.3-flash-free` and `deepseek-v4.1-flash-free`.
> `GET /api/llm/discovery` is the authority on what exists — `llm/catalogue.py`
> is only the curated snapshot.

---

## Tech stack

- **Backend:** Python 3.12 · FastAPI · LangGraph · SQLAlchemy/asyncpg · LiteLLM
- **Knowledge:** PostgreSQL + pgvector · ChromaDB · (optional) Neo4j
- **Web intelligence:** SearXNG (meta-search) · Scout scrape / JS render
- **Frontend:** Vanilla HTML/CSS/JS (Tailwind), loaded from `file://` by Electron
- **Desktop:** Electron — GUI, and it spawns the backend
- **LLM:** OpenCode Zen free models via the `opencode` CLI (**default**, no key) · Zen over HTTP · Ollama (local)

---

## Quick start

### Desktop app

```bash
cd desktop && npm install
npm start              # or: npm run dev  (serves the site over HTTP, reload to apply edits)
```

One command. It brings up postgres and chromadb in Docker, spawns the backend
on the host, and opens the forum.

| script | loads from | use for |
|--------|------------|---------|
| `npm start` | `file://` | normal use |
| `npm run dev` | `http://localhost:5500` | editing the UI — reload picks up changes without restarting Electron |

If the backend cannot start, the app says so with the exact `pip install`
command. The logs are at `~/.config/com-council-of-minds/logs/electron.log`.

> The backend is launched with the interpreter that can actually import
> `uvicorn` — the repo's `.venv`, in practice. If none can, the app tells you
> rather than crash-looping behind a window that looks fine.

The site works with no internet connection — Tailwind, the three webfonts,
`marked` and DOMPurify are all vendored into `com_website/`. Rebuild them
after editing a page's markup or theme:

| script | does |
|--------|------|
| `npm run build:web` | both of the below, in order |
| `npm run build:css` | compiles one stylesheet per page from `com_website/tailwind.config.js` |
| `npm run build:icons` | harvests the Material Symbols glyphs each page renders |
| `npm run build:fonts` | fetches and subsets the fonts to the glyphs in use |
| `node vendor-libs.js` | re-downloads `marked` / DOMPurify into `com_website/vendor/` |

The generated assets are committed, so a fresh clone runs without any of
these. Rebuild only when you change a page's markup or theme.

> Each page compiles from **its own** config entry. The pages deliberately
> differ — `agent_profiles.html` and `history.html` use a smaller `pixel-body`
> than the rest — so collapsing them into one shared theme would silently
> restyle those pages.

### From a terminal

```bash
python start.py      # Docker infra + backend on the host
```

Then open `com_website/forum.html`.

```
=== Starting infrastructure (Docker) ===
  postgres: OK
  chromadb: OK
=== Backend ===
  provider: opencode-cli
    binary: /home/you/.opencode/bin/opencode
    verified: big-pickle answered
```

Nothing else is required — no API key. The CLI has its own credentials.

| flag | effect |
|------|--------|
| `python start.py` | infra + host backend |
| `--local` | also start Ollama in Docker |
| `--graph` | also start Neo4j |
| `--server` | backend only, no Docker |
| `--container` | whole stack in Docker (loses the free models) |

### Just the backend

If you are already running Postgres somewhere and only want the API:

```bash
cd backend
../.venv/bin/python main.py              # add --reload for development
```

Use the virtualenv's interpreter. With the system `python` you get a plain
`ModuleNotFoundError: No module named 'fastapi'` — `main.py` checks for that
and names the venv and the launcher instead of leaving you to guess.
| `--stop` | stop the infra |

### Infrastructure only

```bash
docker compose up -d
```

postgres, chromadb and searxng. Pair it with a backend you run yourself —
`python start.py --server`, or Electron.

Run it from the repo root: `compose.yaml` there is a thin `include` of
`docker/docker-compose.yml`, so `docker compose up -d` works with no `-f`.
`docker compose -f docker/docker-compose.yml up -d` still works too.

> **Ports.** If 5432/8000/8080/8888 are taken, set `POSTGRES_PORT`,
> `CHROMA_PORT`, `FRONTEND_PORT` and `SEARXNG_PORT` in `docker/.env`, and
> mirror them in `backend/.env` (`CHROMA_PORT`, `SEARXNG_URL`,
> `POSTGRES_URL`) — the backend reaches the containers over localhost.

### Configuration

Nothing is required:

```bash
LLM_PROVIDER=opencode-cli     # the default; needs the `opencode` CLI on PATH
```

The free models are reached by shelling out to the OpenCode client, because
**Zen's free tier refuses direct API calls**:

```
POST /zen/v1/chat/completions  →  FreeTierError:
  "OpenCode's free tier can only be used from within OpenCode."
```

`opencode run --model opencode/big-pickle` works, needs no API key, and all six
default models answer through it. The cost is latency: **1.4–3.2s per call**,
so a five-character two-round topic takes ~3–5 minutes.

> **The backend must run on the host** — the Docker image ships no `opencode`
> binary, and the CLI also needs its own credential store. In Docker use
> `LLM_PROVIDER=ollama` (free, local) or `opencode` (Zen over HTTP, billed).
> This is why `python start.py` and Electron both run the backend on the host.

Everything else has a working default, and **the default models are all free**.
See [docs/PROVIDERS.md](docs/PROVIDERS.md).

---

## Feeding topics from an API

```bash
curl -X POST http://localhost:8080/api/forum/intake \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Should we adopt trunk-based development?",
    "source": "jira",
    "payload": {"ticket": "PROJ-42"},
    "open_now": true
  }'
# -> {"intake_id": "...", "topic_id": "...", "status": "opening"}
```

Set `open_now: false` to queue it and let the Moderator drain the queue later.
See [docs/FORUM.md](docs/FORUM.md).

---

## Project structure

```
├── backend/
│   ├── llm/                  provider registry + per-role model binding
│   ├── agents/
│   │   ├── registry.py       the cast, as data
│   │   ├── forum_graph.py    open → statements → exchange → synthesis
│   │   ├── forum_signals.py  deterministic convergence
│   │   ├── forum_characters.py  run one character
│   │   ├── forum_store.py    topics + posts
│   │   ├── moderator.py oracle.py rag_agent.py sage.py   ← chamber path
│   │   └── graph.py critique.py session_memory.py
│   ├── api/                  REST + WebSocket (forum.py, forum_ws.py, llm.py)
│   ├── db/forum_models.py    topics, posts, topic_intake, llm_roles
│   └── tests/
├── com_website/              forum.html, topic.html, llm_settings.html, …
├── desktop/                  Electron shell — GUI + spawns the backend
├── docker/                   compose (infra only), nginx, postgres-init
└── sage_wiki/                persistent knowledge base
```

---

## Tests

```bash
cd backend && pytest -q                                   # 597 unit tests
pytest -q -m live --run-live --allow-real-db               # + Postgres integration
```

The live suite runs real topics end-to-end against Postgres with the LLM
stubbed, and verifies both outcome shapes — consensus and plurality. Both
topic paths are covered: the blocking one and the streamed one, which is what
the UI and the intake endpoint use.

---

## Notes

- **The free models need the OpenCode CLI and a host install.** They are not
  reachable over the Zen API by any standalone client, which is why the CLI is
  the default transport rather than a fallback. Expect 1.4–3.2s per call.
- The old **Chamber** (single-question deliberation) is still present and still
  works: `/chamber`. The forum is the default surface; the chamber is not
  removed.
- Every topic also writes a `Deliberation` record, so Archive, PDF export and
  verdict filing continue to work unchanged.
- The live suite seeds everything it needs and passes on an empty database. A
  fresh database must have pgvector first (`CREATE EXTENSION vector;`) — the
  Docker init script only installs it on first container start, so a
  `CREATE DATABASE` from outside starts without it.
- The live suite **rewrites `backend/okf/bundles/oracle/index.md`** (the
  promotion test creates a document and the bundle index is regenerated).
  `git checkout` it before committing, or you will commit a test artefact.
- `/api/health` reports `configured` and `callable` separately. A key being
  present does **not** mean a model answers; `POST /api/llm/verify` proves it.
- The schema is managed by alembic (`backend/db/migrations/`). `init_db()` still
  creates missing tables and adds missing columns, because `create_all` only
  ever creates tables — but anything needing a backfill belongs in a revision.

## License

[MIT](LICENSE) — © 2026 mkaydin.