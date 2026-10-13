# Docker

```bash
cp docker/.env.example docker/.env
docker compose -f docker/docker-compose.yml --profile local up -d
python start.py                        # -> http://localhost:8080
```

> **The container cannot use the free Zen models.** The default host provider
> (`opencode-cli`) shells out to an `opencode` binary that this image does not
> ship. In Docker use `LLM_PROVIDER=ollama` (free, local — needs
> `--profile local`) or `opencode` (Zen over HTTP, needs a funded account).
> For the free models, run the backend on the host: `python start.py
> --no-docker`.

or directly:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

---

## Services

### Default stack

| service | image | port | purpose |
|---------|-------|------|---------|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | primary store — topics, posts, sessions, vectors |
| `chromadb` | `chromadb/chroma` | 8000 | document vector store |
| `searxng` | `searxng/searxng` | 8888 | meta-search for Divination |

Host ports are configurable (`POSTGRES_PORT`, `CHROMA_PORT`, `SEARXNG_PORT`)
because 5432/8000/8888 are commonly taken by other local projects. **Mirror
them in `backend/.env`** — the backend reaches the containers over localhost:

| `docker/.env` | `backend/.env` |
|---------------|----------------|
| `POSTGRES_PORT` | the port in `POSTGRES_URL` |
| `CHROMA_PORT` | `CHROMA_PORT` |
| `SEARXNG_PORT` | the port in `SEARXNG_URL` |

### `full` profile (not the usual way to run)

| service | image | port | purpose |
|---------|-------|------|---------|
| `fix-permissions` | `alpine` | — | one-shot: chowns the knowledge volumes |
| `backend` | built from `backend/Dockerfile` | 8734 | FastAPI + LangGraph |
| `frontend` | built from `com_website/Dockerfile` | 8080 | nginx; serves the site, proxies `/api` and `/ws` |

No `opencode` binary here, so this profile cannot use the free Zen models.

### `--profile local`

| service | image | port | purpose |
|---------|-------|------|---------|
| `ollama` | `ollama/ollama` | 11434 | the optional local LLM provider |
| `ollama-pull` | `ollama/ollama` | — | one-shot: `ollama pull` for a tag |

```bash
docker compose -f docker/docker-compose.yml --profile local up -d
docker compose -f docker/docker-compose.yml --profile local run --rm ollama-pull
```

### `--profile graph`

| service | image | port | purpose |
|---------|-------|------|---------|
| `neo4j` | `neo4j:5-community` | 7474, 7687 | optional graph backend |

> Neo4j was configured in `config.py` long before this compose file existed, but
> was never actually containerised. This profile closes that gap. The Analyst
> reads it opportunistically and degrades cleanly when it is absent.

---

## Why the frontend container exists

The pages are hand-written HTML that calls `window.COM.api()` and opens a
WebSocket. Under `file://` that needs a real origin and a cross-origin socket.
nginx provides both, and makes the app reachable at `http://localhost:8080`
without a dev server.

It also raises the WebSocket timeouts that matter here:

```nginx
proxy_read_timeout 3600s;   # /ws — a thread lives for minutes
proxy_read_timeout 600s;    # /api — one round is many sequential LLM calls
```

The nginx defaults (60s) would close the socket mid-thread, and the UI would
silently stop receiving posts.

---

## Volumes

| volume | holds | survives rebuild |
|--------|-------|------------------|
| `pgdata` | Postgres | ✅ |
| `chromadata` | Chroma | ✅ |
| `searxngdata` | SearXNG config | ✅ |
| `okfbundle` | the Oracle's OKF knowledge bundle | ✅ |
| `neo4jdata` / `neo4jlogs` | Neo4j (`graph` profile) | ✅ |
| `ollamadata` | pulled models (`local` profile) | ✅ |
| `../sage_wiki` | the Sage's wiki (bind mount) | ✅ |

---

## Permissions

Only relevant to the `full` profile. The backend container runs as the
non-root user `com` (uid 1000); a host backend already has your permissions. Docker creates named
volumes root-owned, so two things need care:

- **`fix-permissions`** runs once before the backend and chowns `/sage_wiki`
  and the OKF bundle to the app user. Without it, filing a verdict or a wiki
  page fails with `PermissionError`.
- **`/tmp/com-logs` has no volume at all.** A named volume *and* an anonymous
  volume are both created root-owned, and either one makes
  `RotatingFileHandler` fail at import — the backend crash-loops before it can
  serve anything. Logs live on the container filesystem; read them with
  `docker compose logs backend` or `docker exec com-backend cat /tmp/com-logs/backend.log`.

> structlog writes JSON to `/tmp/com-logs/backend.log`, not to stdout. `docker
> logs` shows only uvicorn's access log unless `LOG_LEVEL=DEBUG`.

---

## The backend image (`full` profile only)

Multi-stage: wheels are built once with a compiler, then copied into a slim
runtime that never sees a toolchain.

- `python:3.12-slim` (the repo's venv is 3.14 locally; 3.12 is the floor)
- `libpq5` — asyncpg, `libgomp1` — torch's OpenMP
- non-root `USER com`, with a healthcheck on `/api/health`
- `start_period: 120s` — the first boot loads the sentence-transformer model

`.dockerignore` keeps `.venv`, `node_modules`, `raw_data/`, `google-okf/` and
`.env` out of the build context. It deliberately does **not** exclude
`com_website/`, because the frontend image copies it from the same context.

---

## Environment

`docker/.env.example` documents everything. The only required value:

```bash
OPENCODE_API_KEY=...
```

| variable | default | notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `opencode` | or `ollama` |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/v1` | |
| `LLM_MODEL_DEFAULT` | `big-pickle` | free |
| `LLM_MODEL_<ROLE>` | provider default | per character |
| `FRONTEND_PORT` | `8080` | |
| `BACKEND_PORT` | `8734` | |
| `FORUM_ENABLED` | `true` | |
| `FORUM_MAX_ROUNDS` | `2` | one model call per character per round |
| `FORUM_INTAKE_KEY` | *(empty)* | empty = intake is local-only |

---

## The provider inside the container

| `LLM_PROVIDER` | free models | key | notes |
|----------------|-------------|-----|-------|
| `opencode-cli` | ✅ | no | **unavailable here** — no `opencode` binary |
| `opencode` | ❌ | yes + billing | Zen over HTTP, fast |
| `ollama` | ✅ | — | `--profile local`; needs the tag pulled |

Setting `opencode-cli` in Docker produces a health hint that says so
specifically, rather than complaining about PATH — the binary will never be on
PATH in that image, so "install the CLI" would be advice that cannot be acted on.

---

## Healthchecks

`chroma` needed three corrections that are worth knowing if you add others:

- the image ships **no `curl` or `wget`**, so the usual probe command does not
  exist and the container reports *unhealthy* forever;
- `/api/v1/heartbeat` is deprecated and answers **410 Gone** — `/api/v2` is live;
- `CMD-SHELL` runs `/bin/sh` (dash), where bash's `/dev/tcp` does not exist.

The working form is `CMD` + an explicit `bash -c`.

`start.py` also probes Postgres with a **TCP connect**, not an HTTP GET —
Postgres speaks its own wire protocol, so an HTTP probe there never succeeds
and a healthy database reads as unreachable.

---

## Troubleshooting

**Port already allocated.** 5432/8000/8080/8888 all collide easily. Set
`POSTGRES_PORT` / `CHROMA_PORT` / `SEARXNG_PORT` / `FRONTEND_PORT` in
`docker/.env` and mirror them in `backend/.env`.

**Backend restart-looping.** Almost always the log-volume permission problem.
`docker logs com-backend --tail 20`.

**`vector` extension missing.** `docker/postgres-init/01-vector.sql` runs only on
an *empty* `pgdata`. On an existing volume:
`docker exec com-postgres psql -U council -d council -c 'CREATE EXTENSION IF NOT EXISTS vector;'`

**No models / every call fails.** Check the mapping, then *prove* it — a key
being present does not mean a model answers:
```bash
curl -s localhost:8734/api/llm/roles | python3 -m json.tool
curl -s localhost:8734/api/health  | python3 -m json.tool
curl -s -X POST localhost:8734/api/llm/verify -d '{}' | python3 -m json.tool
```
`configured: true` with `callable: null` means nobody has checked. The
`/verify` response carries a `diagnosis` explaining the failure in plain terms.

**A topic never concludes.** Look for `forum_character_failed` in the backend
log. `llm_call_with_retry` does not retry auth errors, so this surfaces in
seconds rather than minutes.

**Reset everything.**
```bash
python start.py --stop
docker compose -f docker/docker-compose.yml down -v
```

---

## Without Docker

```bash
python start.py --server
```

Runs uvicorn on the host with no containers at all — useful when Postgres is
already running elsewhere. Knowledge retrieval degrades if the stores are
missing, but the forum itself works.

The Electron shell does the same thing, and also brings the infra up first.