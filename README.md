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
| **Oracle** | Seeker of Truth | OKF knowledge bundle + raw RAG | `longcat-2.5-preview-free` |
| **Scholar** | Keeper of Lore | vector RAG, documents, archive | `nemotron-3.5-lightning-free` |
| **Analyst** | Weaver of Connections | OKF + graph traversal | `ling-3.1-flash-free` |
| **Historian** | Keeper of Time | wiki, prior sessions, OKF | `step-5-preview-free` |
| **Sage** | Voice of Intuition | wiki | `mimo-v2.6-flash-free` |
| **Moderator** | Master of Rules | the whole thread | `big-pickle` |

> The characters differ by **how they think**, not just by which backend they
> query — that distinction is the difference between a forum and one voice in
> five fonts. See `backend/agents/registry.py`.

Every binding is overridable per character, at runtime, from
**`/llm_settings.html`** or `POST /api/llm/roles`.

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
cd desktop && npm install && npm start
```

Electron brings up postgres and chromadb in Docker, spawns the backend on the
host, and opens the forum. One command.

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
| `--stop` | stop the infra |

### Infrastructure only

```bash
docker compose -f docker/docker-compose.yml up -d
```

postgres, chromadb and searxng. Pair it with a backend you run yourself —
`python start.py --server`, or Electron.

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
cd backend && pytest -q                                   # 325 unit tests
pytest -q -m live --run-live --allow-real-db               # + Postgres integration
```

The live suite runs real topics end-to-end against Postgres with the LLM
stubbed, and verifies both outcome shapes — consensus and plurality.

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
- Two live tests in `test_rag_retrieval.py` fail on a fresh database — they
  need a seeded corpus. They fail identically on the pre-forum commits.
- `/api/health` reports `configured` and `callable` separately. A key being
  present does **not** mean a model answers; `POST /api/llm/verify` proves it.

## License

[MIT](LICENSE) — © 2026 mkaydin.