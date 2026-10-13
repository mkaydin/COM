# Council of Minds — Forum Migration Plan

**Status:** proposed · **Date:** 2026-10-13 · **Branch:** `forum-v2`

---

## 1. Current state (what exists today)

### Architecture

```
com_website/*.html (vanilla + Tailwind CDN)   desktop/ (Electron shell)
        │  REST + WebSocket                        │ spawns uvicorn
        ▼                                          ▼
            FastAPI (backend/main.py, :8734)
              ├─ agents/graph.py  ← LangGraph workflow
              │    router → {oracle, rag, sage} (parallel) → moderator → [critique ⇄ moderator] → END
              │    bounded by max_critique_rounds=2
              ├─ agents/{oracle,sage,rag_agent}.py  — one knowledge source each
              ├─ agents/critique.py   — hold / revise / concede on flagged conflicts
              ├─ agents/moderator.py  — deterministic alignment + conflicts + decision_space
              ├─ agents/session_memory.py — prior-deliberation digest for follow-ups
              └─ api/{sessions,deliberations,verdicts,agents,wiki,divination,okf,rag,documents,websocket}.py
              DB: PostgreSQL+pgvector (10 tables), ChromaDB, Neo4j (configured, unused), SearXNG
```

### Health

- `187 passed, 30 skipped` (`pytest` from `backend/`). Good baseline.
- Commits: `c264b0b more features more bugs` is HEAD. Working tree clean.

### Findings that shape the plan

| # | Finding | Impact |
|---|---------|--------|
| F1 | **Characters are hardcoded.** `oracle.py`, `sage.py`, `rag_agent.py` each bake in one knowledge source and one JSON schema. `COUNCIL_AGENTS = ("oracle","rag","sage")` is a literal tuple in `moderator.py`, `critique.py`, `websocket.py`. | A "forum of characters" needs a **data-driven registry**. Characters are currently code, not config. |
| F2 | **One model for everyone.** `config.py` hardcodes Ollama-only: `llm_model: str = "openai/gemma4:e4b"`, no provider registry, no per-agent model. | Per-character models are impossible today. This is the single biggest blocker for the "free models per character" requirement. |
| F3 | **Round-trip, not a thread.** Each query creates a fresh `Deliberation` row. Follow-ups are separate deliberations stitched by `session_memory.py` prose digest. | There is no persistent thread, no per-post authorship, no ordering, no "moderator opens a topic". |
| F4 | **Critique is an afterthought.** Only fires on `severity == "factual"` conflicts or confidence spread > 0.45. Agents never read each other's *prose*; they only see a one-line position string. | Real "characters talking to each other" needs **turn-based, reply-targeted** exchange, not a parallel fan-out plus a dispute resolver. |
| F5 | **Neo4j is configured but absent.** `neo4j_url` in `config.py` + `.env.example`; **no `neo4j` service in `docker/docker-compose.yml`**. `db/neo4j.py` exists. | Docker compose is incomplete. |
| F6 | **`start.py` hardcodes an Ollama health probe** (`curl http://localhost:11434/api/tags`). If Ollama becomes optional, startup breaks. | Must become provider-aware. |
| F7 | **Doc drift.** README says "any LiteLLM-supported provider"; `config.py` says "exclusively Ollama … no provider registry". | Resolve as part of F2. |
| F8 | **Embedding dim mismatch.** `Document.embedding` is `Vector(1536)`, `RagChunk.embedding` is `Vector(384)`. Chroma has no healthcheck. | Out of scope for this migration, but noted. |

---

## 2. Target architecture

```
                    ┌─────────────────────────────────────────┐
  user question ───► │  Moderator  ── opens a Topic            │
  API intake  ────► │  (frames it, titles it, picks the cast)  │
                    └────────────────────┬────────────────────┘
                                         ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  Thread: posts[]  — ordered, each authored by one character     │
   │                                                                 │
   │   Round 0  Oracle   ▸ opening statement (evidence)              │
   │            Scholar  ▸ opening statement (retrieval + citations)  │
   │            Sage     ▸ opening statement (analogy / ethics)       │
   │                                                                 │
   │   Round 1  Oracle   ▸ replies to Scholar's claim #2  (support)  │
   │            Scholar  ▸ replies to Oracle's claim #1  (challenge) │
   │            Sage     ▸ challenges the shared premise              │
   │            … bounded by max_rounds                               │
   └──────────────────────────┬──────────────────────────────────────┘
                              ▼
                    ┌───────────────────────┐
                    │  Moderator synthesis  │
                    │  mode = consensus ──► ONE answer, signed by all
                    │  mode = diverse   ──► 2–4 distinct answers,
                    │                          each with its backers
                    └───────────────────────┘
```

**Two outcomes, both first-class** (per your requirement — *either* different answers *or* one accepted answer):

| Mode | Trigger | Result |
|------|---------|--------|
| `consensus` | ≥2 characters converge on substantively the same claim, **and** the moderator's agreement check passes | One synthesized answer + the list of characters that stand behind it + any dissents preserved |
| `diverse` | Real disagreement survives `max_rounds` | 2–4 distinct positions, each attributed to its supporters, ranked, with the genuine fault line named |

Mode is chosen **per topic** by the moderator from the evidence, and is overridable by the user up front.

---

## 3. Work breakdown

### Phase 1 — Provider layer (unblocks everything)

**New package `backend/llm/`**

```
backend/llm/
  __init__.py       — llm_complete(), llm_health(), model_for(role)
  providers.py      — PROVIDERS registry
  registry.py       — free-model catalogue (opencode) + ollama passthrough
  errors.py         — ProviderError, ModelNotFoundError
```

`config.py` becomes a thin settings holder. Every agent calls `llm.llm_complete(...)` instead of `config.llm_complete(...)`.

**Providers**

| id | default | base_url | auth | notes |
|----|---------|----------|------|-------|
| `opencode` | ✅ yes | `https://opencode.ai/zen/v1` | `OPENCODE_API_KEY` | OpenAI-compatible `/chat/completions`. Verified live: `GET /zen/v1/models` returns 78 models, 11 free. |
| `ollama` | optional | `http://$OLLAMA_HOST:$OLLAMA_PORT/v1` | placeholder | activated via `LLM_PROVIDER=ollama` or the `local` compose profile |

**Per-role model binding** — the core of "free models for each character":

```env
LLM_PROVIDER=opencode
OPENCODE_API_KEY=sk-...
OPENCODE_BASE_URL=https://opencode.ai/zen/v1

# Per-character. Any role omitted falls back to LLM_MODEL_DEFAULT.
LLM_MODEL_MODERATOR=big-pickle
LLM_MODEL_ORACLE=longcat-2.5-preview-free
LLM_MODEL_SCHOLAR=nemotron-3.5-lightning-free
LLM_MODEL_SAGE=mimo-v2.6-flash-free
LLM_MODEL_DEFAULT=big-pickle
```

Every role is independently overridable at runtime via `POST /api/llm/roles` (persisted to a small `llm_roles` table so it survives restarts).

**Free-model catalogue — verified against `GET /zen/v1/models` on 2026-10-13**

`$0` input/output, `/v1/chat/completions` compatible — usable directly via LiteLLM's `openai/` prefix:

| model | id | proposed role |
|-------|----|---------------|
| Big Pickle | `big-pickle` | Moderator (default / general) |
| LongCat 2.5 Preview Free | `longcat-2.5-preview-free` | Oracle (long-context evidence) |
| Nemotron 3.5 Lightning Free | `nemotron-3.5-lightning-free` | Scholar (fast, retrieval-heavy) |
| MiMo-V2.6-Flash Free | `mimo-v2.6-flash-free` | Sage (creative, high-temp) |
| Ling 3.1 Flash Free | `ling-3.1-flash-free` | reserve / Historian |
| Step 5 Preview Free | `step-5-preview-free` | reserve / Analyst |
| Cat Jam Free | `cat-jam-free` | reserve |
| Space Bunny Free | `space-bunny-free` | reserve |

Caveats found:
- `muse-spark-1.2/1.3-contributor-free` are free but served on **`/v1/responses`**, not chat-completions → excluded from the chat default set; noted as a future provider-special-case.
- `exo-free` appears in the OpenCode model catalog but **not** in the live `/v1/models` response — likely transient; not used as a default.
- `jev-1.13-free` uses the bespoke `/v1/systemone` endpoint — not a chat model.
- Paid fallbacks (e.g. `claude-*`, `gpt-*`, `gemini-*`) stay fully selectable; only the **defaults** are free.

Because these are free-tier endpoints, every agent call keeps the existing `retry.py` backoff, and the moderator degrades to `_fallback_synthesis` rather than failing the thread.

**Changes**
- `config.py`: add `llm_provider`, `opencode_api_key`, `opencode_base_url`, `llm_model_default`, `llm_model_<role>`; deprecate `llm_model`.
- `ollama_client.py`: keep, but scope to the ollama provider only.
- `main.py`: `/api/health` gains a provider block; new `/api/llm/providers`, `/api/llm/models?provider=`, `/api/llm/roles`, `POST /api/llm/test`.
- `start.py`: provider-aware health probe.
- Delete the "Ollama exclusively" comment blocks.

---

### Phase 2 — Character registry (F1)

**New `backend/agents/registry.py`** — the cast becomes data.

```python
Character(
  key="oracle",
  display_name="The Oracle",
  archetype="Seeker of Truth",
  color="runed-gold", icon="auto_awesome",
  model_role="ORACLE",            # → llm.llm_complete resolves the model
  temperature=0.3, max_tokens=2048,
  knowledge=["okf", "rag_chunks"],
  system_prompt=LOCAL_ORACLE_PROMPT,
  response_model=OracleResponse,
  voice_rules=["cite every claim", "say 'insufficient data' rather than guess"],
  default_stances=["support","challenge","concede"],
)
```

Existing three are ported verbatim (no behaviour change), plus two new characters from `old_docs/character_persona.md`:

- **The Analyst** — relationship/causal reasoning, outputs chains not paragraphs.
- **The Historian** — temporal, "what did we already conclude about this".

Every consumer of the hardcoded `COUNCIL_AGENTS` tuple switches to `registry.keys()`. `moderator.py`, `critique.py`, `websocket.py`, `api/agents.py` all become registry-driven.

---

### Phase 3 — Forum domain model

**New migration `backend/db/migrations/versions/<rev>_forum_threads.py`**

```sql
topics
  id            uuid pk
  session_id    uuid null references sessions(id)     -- optional link to Archive
  title         text not null
  origin        text not null   -- 'user' | 'api' | 'moderator'
  question      text not null   -- the raw seed question
  framing       text            -- moderator's framing of the question
  mode          text not null   -- 'consensus' | 'diverse' | 'auto'
  status        text not null   -- 'open' | 'debating' | 'concluded' | 'archived'
  outcome       text            -- 'consensus' | 'plurality' | null
  cast          jsonb           -- character keys, ordered
  tags          jsonb
  source_meta   jsonb           -- API intake payload, request id, etc.
  created_at / concluded_at

posts
  id             uuid pk
  topic_id       uuid not null references topics(id) on delete cascade
  seq            int  not null                 -- monotonic ordering, no ties
  round          int  not null
  author_kind    text not null                 -- 'user'|'moderator'|'character'
  author         text not null                 -- character key or 'moderator'|'user'
  body           text not null                 -- markdown
  stance         text not null                 -- 'open'|'propose'|'support'|'challenge'|'question'|'concede'|'synthesize'
  reply_to       uuid null references posts(id)
  confidence     float null
  claims         jsonb                         -- structured claims extracted from the body
  citations      jsonb
  extra          jsonb                         -- reasoning, stats, raw agent output
  created_at
  unique (topic_id, seq)

topic_intake                     -- API-fed question queue, moderator drains it
  id, source, payload jsonb, question text, status ('queued'|'opened'|'rejected'),
  topic_id uuid null, received_at, opened_at
```

Deliberation mapping: each topic run writes a `Deliberation` + `AgentResponse` rows so **Archive / PDF export / verdict filing keep working unchanged**. The forum is an authoring surface, not a replacement for the record.

Indexes: `posts(topic_id, seq)`, `topics(status, created_at desc)`, `topics(session_id)`, `topic_intake(status)`.

---

### Phase 4 — Forum orchestration

**New `backend/agents/forum_graph.py`** (LangGraph, sibling of `graph.py` — old graph stays for `POST /sessions/{id}/deliberate`).

State: `topic_id`, `question`, `cast`, `round`, `max_rounds`, `posts`, `mode`, `outcome`, `signals`.

| Node | Does |
|------|------|
| `open_topic` | Moderator reads the raw question → returns `title`, `framing`, `tags`, ordered `cast`, `mode` (`auto` resolves here), and writes the opening post. |
| `statements` | `asyncio.gather` over the cast. Each runs its normal retrieval pipeline + its own model. Output: one `propose` post each. |
| `exchange` | Per round, per character, **sequentially ordered by cast** so each can read the round above it. Prompt includes: the opening post, all posts so far (truncated, relevance-ranked), and this character's own past posts. Output: one post with `stance` + `reply_to`. |
| `moderator_synthesis` | Computes deterministic signals (agreement clusters, confidence spread, unresolved challenges) → picks mode → produces either a `consensus` answer or 2–4 `plurality` positions, each with its backers. Writes the `synthesize` post. |

Conditional edges: `exchange → moderator_synthesis → END` when converged, else `→ exchange` while `round < max_rounds`, then a forced `moderator_synthesis` (identical code path, `force_diverse=True`).

**Deterministic convergence check** (`agents/forum_signals.py`), so this isn't left to the model:
- Normalize each character's final claim text → content-word token set → Jaccard against every other pair.
- Agreement = pair overlap ≥ 0.35 **and** no unresolved `challenge` posts between them.
- Convergence = agreement graph has a single component covering ≥ 2/3 of the cast **and** mean confidence spread < 0.25.
- Always reported in the UI as a visible badge, never silently trusted.

**Reuse:** `json_utils.extract_json`, `retry.llm_call_with_retry`, `session_memory`, OKF/RAG/wiki retrieval — all unchanged. Characters gain *style*, not new infrastructure.

---

### Phase 5 — API surface

**REST — new `backend/api/forum.py`**

```
POST   /api/forum/topics                     open a topic from a question  → runs the graph
POST   /api/forum/topics/stream              same, but answers over SSE
GET    /api/forum/topics                     list (filter: status, mode, tag, cast, session)
GET    /api/forum/topics/{id}                topic + posts + signals + outcome
PATCH  /api/forum/topics/{id}                rename, change mode/tags, archive
DELETE /api/forum/topics/{id}
GET    /api/forum/topics/{id}/posts          paginated, `?since_seq=` for catch-up
POST   /api/forum/topics/{id}/posts          user reply → triggers another exchange round
POST   /api/forum/topics/{id}/close          force-close with a moderator summary
POST   /api/forum/topics/{id}/reopen

POST   /api/forum/intake                     API question intake (single or batch)
GET    /api/forum/intake                     queue state
DELETE /api/forum/intake/{id}
```

`/api/forum/intake` is the "or from api" path: an external system POSTs a question, it lands in `topic_intake` as `queued`, and either returns immediately with a `topic_id` (opened inline) or lets the moderator drain the queue. Auth via `X-COM-Key` compared to `FORUM_INTAKE_KEY` (optional; off by default = local-only).

**WebSocket — extend `api/websocket.py`**

```
WS /ws/forum/{topic_id}     live thread
  → topic_opened        {topic, opening_post}
  → round_started       {round, participants}
  → post_created        {post}                      (fires per post, as written)
  → round_completed     {round, signals}
  → synthesis           {mode, outcome, answer|positions}
  → topic_concluded     {topic_id, outcome}
  → error               {message}
```

Client sends `{"type":"reply", "body": "..."}` to post as the user mid-thread. The existing `/ws/sessions/{id}/stream` is left intact and reimplemented on top of the forum engine so old clients keep working.

---

### Phase 6 — Frontend

| File | Change |
|------|--------|
| `com_website/forum.html` | **new** — topic board: list, filter, "New Topic" composer (mode picker, cast picker, "import from API"), status badges. |
| `com_website/topic.html` | **new** — the thread. Ordered posts, per-character avatar/color, stance chips, reply-to indentation, live streaming, convergence badge, consensus/plurality card, "your answer" box. |
| `com_website/council_chamber.html` | kept, becomes "Chamber (legacy)" link in nav; `ws.js`/`api.js` gain forum methods alongside existing ones. |
| `com_website/setup.html` | rewritten — provider picker (OpenCode default / Ollama local), API key field, free-model dropdown per role, connection test. |
| `com_website/agent_profiles.html` | reads the registry from `/api/agents`; renders every registered character incl. new ones. |
| `com_website/council_landing_page.html` | hero → "Open a topic"; the 4-card council strip becomes a cast strip. |
| all pages | nav: `Chamber` → `Forum` + `Board`; add `cast.html` if registry outgrows `agent_profiles`. |

Design language (VT323 pixel, obsidian/parchment/runed-gold, Material Symbols, Tailwind CDN, `marked`+`DOMPurify`) is preserved — the forum reads as the same app.

---

### Phase 7 — Docker

**`docker/docker-compose.yml` — full stack, all COM containers**

| service | image / build | profile | notes |
|---------|---------------|---------|-------|
| `postgres` | `pgvector/pgvector:pg16` | default | + `CREATE EXTENSION vector` on init; add missing healthcheck gap |
| `chromadb` | `chromadb/chroma` | default | **add healthcheck** (F8) |
| `searxng` | `searxng/searxng` | default | keep |
| `backend` | **built** from `backend/Dockerfile` | default | `uvicorn main:app`; waits on postgres/chroma healthy; runs `alembic upgrade head`; mounts `sage_wiki/` + `okf/bundles/` as volumes |
| `frontend` | **built** from `com_website/Dockerfile` (nginx) | default | serves the site, proxies `/api` + `/ws` → `backend`; makes COM reachable at `http://localhost:8080` |
| `ollama` | `ollama/ollama` | `local` | **optional provider**; volumes for models; not started by default |
| `ollama-pull` | same image | `local` | one-shot `ollama pull` for the configured local model |
| `neo4j` | `neo4j:5-community` | `graph` | **closes F5** — was configured in code, never containerized |

```bash
docker compose -f docker/docker-compose.yml up -d --build          # default stack
docker compose -f docker/docker-compose.yml --profile local up -d   # + local Ollama
docker compose -f docker/docker-compose.yml --profile graph up -d   # + Neo4j
```

**New files:** `backend/Dockerfile` (python:3.12-slim, non-root, layered deps), `com_website/Dockerfile` + `com_website/nginx.conf`, root `.dockerignore`, `docker/.env.example`, `docker/README.md`.

**`start.py`** → becomes a thin wrapper over compose (up `--wait`, then print URLs); keeps a `--no-docker` path for local dev.

---

### Phase 8 — Tests

New:
- `test_provider_registry.py` — role→model resolution, per-role override, provider switch, unknown-role fallback.
- `test_character_registry.py` — every character has a model role, a schema, a prompt, a color; no dangling `reply_to` casts.
- `test_forum_graph.py` — node wiring, round bound terminates under an always-disagreeing cast, forced synthesis path.
- `test_forum_signals.py` — Jaccard agreement clusters, convergence thresholds, the 2/3 + spread gate.
- `test_forum_api.py` — topic CRUD, `?since_seq=` catch-up, intake queue transitions, `X-COM-Key`.
- `test_post_sequencing.py` — concurrent `exchange` posts get unique monotonic `seq` (no `UniqueViolation` under gather).

Updated:
- `test_graph.py` — replace hardcoded `COUNCIL_AGENTS` assertions with registry assertions.
- `test_health.py` — provider-aware health.

Kept green: the full existing 217-test suite.

---

### Phase 9 — Docs

- `README.md` — forum-first (fixes F7), new mermaid thread diagram, provider table, compose profiles, character table.
- `docs/FORUM.md` — domain model, state machine, convergence rules, API reference.
- `docs/PROVIDERS.md` — opencode/ollama setup, free-model catalogue + date-verified list, per-role binding, rate-limit behaviour.
- `docs/DOCKER.md` — services, profiles, volumes, troubleshooting.
- `old_docs/PLAN.md` — superseded; keep for history, banner it.

---

## 4. Sequencing & risk

```
Phase 1 provider ──────────┐
Phase 2 registry ──────────┼──► Phase 3 schema ──► Phase 4 graph ──► Phase 5 API ──► Phase 6 UI
Phase 7 docker (parallel)──┘
                                          Phase 8 tests run continuously
```

| Risk | Mitigation |
|------|-----------|
| Free-tier models rate-limit a 5-character thread (≈20 calls/topic) | per-role model diversity spreads load; `retry.py` backoff; `max_rounds` default 2; cache identical role calls |
| Free models produce malformed JSON | `extract_json` already handles fences/truncation; `schema_hint()` for stricter models; every node has a deterministic fallback |
| Migration on a DB with existing data | new tables only — additive, no data migration, no destructive change |
| Forum replaces the working chamber | old graph + WS + endpoints stay; new surface is additive; feature flag `FORUM_ENABLED` |
| `op.add` reducer on `posts` breaks `seq` uniqueness | `exchange` writes posts **sequentially per round**, not via `gather`; `seq` assigned inside the writer under a row lock |

---

## 5. Decisions (locked 2026-10-13)

1. **Old chamber:** ✅ keep both, forum is the default surface (behind `FORUM_ENABLED`).
2. **Cast size:** ✅ 5 — Oracle, Scholar, Sage + Analyst and Historian from `old_docs/character_persona.md`.
3. **Knowledge wiring:** each character stays bound to its own source (Oracle→OKF, Scholar→RAG, Sage→wiki, Analyst→graph/causal, Historian→archive+wiki). No cross-contamination — differences come from *how they think*, not from a shared blob.
4. **Model assignment:** ✅ as proposed — Moderator `big-pickle`, Oracle `longcat-2.5-preview-free`, Scholar `nemotron-3.5-lightning-free`, Sage `mimo-v2.6-flash-free`, plus a model-bench endpoint to A/B before/after.
5. **Compose default:** ✅ full stack — `backend` + `frontend` are part of the default `up -d --build`; Ollama and Neo4j behind `--profile local` / `--profile graph`.

---

## 6. Definition of done

- [ ] `docker compose -f docker/docker-compose.yml up -d --build` brings up postgres, chroma, searxng, backend, frontend — all healthy.
- [ ] `--profile local` adds Ollama; `LLM_PROVIDER=ollama` works with no other change.
- [ ] `POST /api/forum/intake` with a question creates a topic and a full thread.
- [ ] A topic can end in **consensus** (one answer, signed) *or* **plurality** (2–4 distinct attributed answers) — both reachable in practice.
- [ ] Each character runs on its own configured model; `GET /api/llm/roles` shows the live mapping.
- [ ] All ≥2 characters, all default models free, no paid key required beyond `OPENCODE_API_KEY`.
- [ ] Forum UI streams posts live and renders both outcome shapes.
- [ ] Archive, PDF export, verdict filing, OKF/wiki filing still work.
- [ ] Full test suite green, including the new forum/provider/registry tests.