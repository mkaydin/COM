# Providers

The Council runs five characters, each potentially on a different model. This
document is how that works and how to change it.

## Providers

| id | default | transport | key | notes |
|----|---------|-----------|-----|-------|
| `opencode-cli` | ✅ host | shells out to `opencode run` | not needed | **The only route to the free models.** |
| `opencode` | | HTTP `/chat/completions` | `OPENCODE_API_KEY` | Needs a **funded** account. Fast. |
| `ollama` | ✅ in Docker | HTTP `/chat/completions` | — | Free, local, private. |

The HTTP providers are reached through LiteLLM's `openai/` prefix with an
explicit `api_base` and `api_key`. The environment is never consulted for
these — this project once talked to whatever proxy happened to be exported in
the shell.

Adding a provider: one `Provider` in `backend/llm/providers.py` and one branch
in `BUILDERS`. Nothing else changes.

---

## Why the CLI is the default

Zen's free models **cannot** be called over the plain API. Verified on
2026-10-13 against a valid key:

```
POST /zen/v1/chat/completions   (all 11 free models)
  → FreeTierError: "OpenCode's free tier can only be used from within OpenCode."

POST /zen/v1/chat/completions   (paid models, unfunded account)
  → "Insufficient account funds"
```

The same models work from the OpenCode client, so `opencode-cli` shells out to
it. **No API key is required** — the CLI uses its own credential store.

### The trade-off, stated plainly

| | `opencode-cli` | `opencode` (HTTP) |
|---|---|---|
| free models | ✅ | ❌ refused |
| needs a key | no | yes, **and billing** |
| latency | **1.4–3.2s/call** | ~0.4s/call |
| needs the CLI on PATH | ✅ | — |
| works in Docker | ❌ | ✅ |

A five-character, two-round topic makes ~15 calls, so expect **~3–5 minutes**
rather than ~20 seconds. `FORUM_MAX_ROUNDS=1` and a three-character cast is a
reasonable first configuration.

### Not available in Docker

The backend image does not ship an `opencode` binary, and adding one plus a
credential mount is more machinery than the alternatives cost. Inside the
compose stack:

```bash
LLM_PROVIDER=ollama     # free, local, no key  (--profile local)
LLM_PROVIDER=opencode   # Zen over HTTP, billed
```

If you set `LLM_PROVIDER=opencode-cli` in Docker, the health endpoint says so
specifically rather than complaining about PATH.

---

## The free defaults

Verified against the live Zen catalogue on **2026-10-13** —
`GET https://opencode.ai/zen/v1/models` returned 78 models, 11 free.

| model | default role | why |
|-------|--------------|-----|
| `big-pickle` | Moderator | the job is structured synthesis + JSON, not depth |
| `longcat-2.5-preview-free` | Oracle | long context; reasons over whole concept bodies |
| `nemotron-3.5-lightning-free` | Scholar | fast; makes many calls with large grounded context |
| `mimo-v2.6-flash-free` | Sage | used at high temperature for analogy and ethics |
| `ling-3.1-flash-free` | Analyst | |
| `step-5-preview-free` | Historian | |

Spreading one model per role is deliberate: these are all free-tier, and
distinct models mean distinct rate-limit buckets.

**This is a snapshot, not a guarantee.** Zen rotates models. If a default 404s,
pick another from the list below, or set `LLM_MODEL_DEFAULT` and let the
per-role catalogue fall back to it.

### Deliberately excluded

| model | why |
|-------|-----|
| `muse-spark-1.2/1.3-contributor-free` | free, but served on `/v1/responses`, not chat-completions |
| `jev-1.13-free` | the bespoke `/v1/systemone` endpoint, not a chat model |
| `exo-free` | in the OpenCode catalog but absent from the live `/v1/models` response — transient |

`/api/llm/models` returns all three with reasons, so the UI can explain the
gap instead of silently hiding it.

### Paid models

Fully selectable, never default: `claude-sonnet-4-6`, `claude-opus-4-8`,
`gpt-5.4`, `gpt-5.4-mini`, `gemini-3.5-flash`, `deepseek-v4-pro`, `glm-5.3`,
`kimi-k3`.

---

## Configuration

```bash
# backend/.env — or docker/.env for the compose stack, which cannot use the CLI
LLM_PROVIDER=opencode-cli
OPENCODE_API_KEY=        # not needed for the free tier
OPENCODE_BASE_URL=https://opencode.ai/zen/v1

LLM_MODEL_DEFAULT=big-pickle

# Per character. Blank = use the provider's default for that role.
LLM_MODEL_MODERATOR=
LLM_MODEL_ORACLE=
LLM_MODEL_SCHOLAR=
LLM_MODEL_SAGE=
LLM_MODEL_ANALYST=
LLM_MODEL_HISTORIAN=
```

### Resolution order

A role resolves **a provider and a model**, and they are resolved together —
the model comes from the *role's own* provider's defaults, so a character
pinned to Ollama never silently receives a Zen model.

Provider (`provider_for("ORACLE")`):

1. the `provider` column of that role's `llm_roles` row (`NULL` = the global
   default, which is what every row written before the column existed means)
2. `LLM_PROVIDER_ORACLE`
3. the global `LLM_PROVIDER`

Model (`model_for("ORACLE")`), against that provider:

1. the role's `llm_roles` override
2. `LLM_MODEL_ORACLE`
3. that provider's built-in default for the role
4. `LLM_MODEL_DEFAULT`
5. any default for that provider

An unknown provider id raises rather than falling back — silently reverting to
the global default would change which models run without saying so.
`routing_for` reports it as `provider_error` so the UI can show it.

Overrides live in Postgres, so a choice made in the UI survives a restart.

### Per-character providers

Because the provider is per role, the cast can be split:

```bash
# the Oracle local, everyone else on free Zen models
curl -X POST localhost:8734/api/llm/roles -H 'Content-Type: application/json' \
  -d '{"role":"ORACLE","model_id":"qwen3:4b","provider":"ollama"}'
```

### Embedding models

Separate from the cast: one embedding model is shared by retrieval, the OKF
bundle and ingestion. Backends are `ollama`, `local`
(sentence-transformers), and the two OpenCode transports.

```bash
GET  /api/llm/embedding        the current selection
POST /api/llm/embedding        {backend, model}
POST /api/llm/embedding/test   embed a probe, report the width
```

**Recommended: `ollama` + `qwen3-embedding:0.6b`.** Measured on an RTX 4060 /
RTX 5060 Ti with Ollama 0.40:

| | batch (32×440 tok) | single query | window | dims |
|---|---|---|---|---|
| `qwen3-embedding:0.6b` @384 | 62 chunks/s | **6.0 ms** | 32k | 1024 → folded to 384 |
| `all-MiniLM-L6-v2` (old default) | 754 chunks/s | 2.2 ms | **256** | 384 |

Two caveats worth stating plainly:

- The old model is ~12x faster per batch. At 6 ms per query the difference is
  invisible next to a 1.4–3.2 s LLM call, but a large re-ingest is slower.
- The old model's **256-token window was a live correctness bug**, not a
  quality setting: `rag.chunker` emits 300–500 token chunks, so 32% of the OKF
  bundle and 92% of the wiki were being discarded before they were ever
  searchable. `encode` returns a shorter vector rather than raising.

> **The vector stores are 384-dimensional.** A wider model is *folded* to 384
> where it supports `dimensions` (the backend sends it), which is how
> `qwen3-embedding` fits with no migration. Fixed-width models that cannot fold
> — `bge-m3`, `nv-embed`, `text-embedding-ada-002` — are rejected.
>
> **Changing the model invalidates every stored vector.** The new model lives
> in a different vector space; stored chunks must be re-embedded. There is no
> re-embed command yet.

### The legacy `LLM_MODEL`

The pre-forum setup was Ollama-only with a single `LLM_MODEL`. It is still
honoured, but only when `LLM_MODEL_DEFAULT` was *not* set — detected with
`model_fields_set`, not by comparing against a literal, so an operator who
deliberately set `LLM_MODEL_DEFAULT=big-pickle` while an old `LLM_MODEL`
lingers in the file gets the one they meant.

---

## Using Ollama locally

```bash
docker compose -f docker/docker-compose.yml --profile local up -d
ollama pull gemma4:e4b

# then
LLM_PROVIDER=ollama LLM_MODEL_DEFAULT=gemma4:e4b \
  docker compose -f docker/docker-compose.yml --profile local up -d
```

Switching providers never re-points a character at a remote model:
`OLLAMA_ROLE_DEFAULTS` is a separate table, and every role falls back to the
same local tag.

> Local models are small. They will produce worse JSON than the Zen defaults,
> and `FORUM_MAX_ROUNDS=1` is a sensible starting point.

---

## API

```
GET    /api/llm/providers          every provider, whether it is configured
GET    /api/llm/roles              the live role → (provider, model) mapping
POST   /api/llm/roles              {role, model_id, provider?}  persists, effective now
DELETE /api/llm/roles/{role}       back to env/default
GET    /api/llm/models             curated free catalogue + installed local tags
GET    /api/llm/discovery          live listing for every provider, chat + embedding
GET    /api/llm/embedding          the selected embedding backend/model
POST   /api/llm/embedding          {backend, model}
POST   /api/llm/embedding/test     embed a probe, report the vector width
POST   /api/llm/test               {model_id, provider?} — send a real completion
POST   /api/llm/reload             re-read persisted overrides
```

`GET /api/llm/roles`:

```jsonc
{
  "provider": "opencode-cli",
  "base_url": "opencode://cli",
  "roles": [
    {"role": "MODERATOR", "model": "big-pickle",
     "routed_model": "big-pickle", "provider": "opencode-cli",
     "provider_source": "global", "transport": "cli_provider",
     "source": "default", "is_free": true,
     "character": null, "character_name": null},
    {"role": "ORACLE", "model": "qwen3:4b", "provider": "ollama",
     "provider_source": "runtime", "routed_model": "openai/qwen3:4b",
     "provider_error": null, "…": "…",
     "character": "oracle", "character_name": "The Oracle"}
  ]
}
```

`source` is `runtime` · `env` · `default` · `fallback`.
`provider_source` is `runtime` · `env` · `global`. Both are shown in the UI, so
you can always tell why a character is on a given model *and* a given backend.
`provider_error` is non-null when the configured provider id does not exist.

> `GET /api/llm/discovery` is the live view (`opencode models` for Zen, roughly
> 600 entries including embedding models; `/api/tags` for Ollama). It never
> raises on an unreachable provider — it reports `reachable: false` plus a
> `hint`, because a settings page that 500s when Ollama is down cannot explain
> that Ollama is down.

### The UI

`/models.html` — per-character provider and model dropdowns, the embedding
model with a width check, and a reset for anything pinned.
`/llm_settings.html` still shows the curated free catalogue and is kept
reachable for existing bookmarks.

---

## Health

`GET /api/health` reports the provider, every resolved role, and a hint:

```jsonc
{
  "status": "degraded",
  "provider": "opencode",
  "llm_ok": false,
  "hint": "OPENCODE_API_KEY is not set — every agent call will fail.",
  "roles": [ /* every role, resolved */ ],
  "postgres_ok": true
}
```

`/api/health/ready` additionally requires Postgres.

For the optional local provider the same endpoint carries `ollama_reachable`,
`model_present` and `installed`.

---

## Failure behaviour

| situation | behaviour |
|-----------|-----------|
| CLI not on PATH | raises before the call, naming the right remedy for host vs container |
| CLI returns no text | raises with the exit code and the tail of stderr |
| no API key (CLI provider) | fine — the CLI has its own credentials |
| no API key (HTTP provider) | raises before the call — one clear error, not five silent ones |
| bad key (401) | **not retried** — fails on the first attempt |
| rate limit (429) | retried with exponential backoff |
| server error (5xx) | retried |
| model 404s | **not retried** |
| moderator unreachable | deterministic fallback synthesis from the signals |

The no-retry rule for auth errors matters more than it looks: a forum run makes
one call per character per round, so retrying a missing key three times with
backoff cost ~50s per thread before the error was even visible.

---

## Adding a provider

```python
# backend/llm/providers.py

def _my_provider() -> Provider:
    s = get_settings()
    return Provider(
        id="myprovider",
        label="My Provider",
        base_url=s.my_base_url,
        api_key=s.my_api_key,
        requires_key=True,
        role_defaults={"MODERATOR": "...", "ORACLE": "..."},
        notes="…",
    )

_BUILDERS["myprovider"] = _my_provider
```

Then add the settings fields, and register the catalogue in
`llm/catalogue.py` if it has free models.

For a **non-HTTP** transport, set `transport="my_module"` and implement
`complete(messages, *, model, **kwargs) -> {"choices": [...]}` in
`llm/my_module.py`. `llm_complete()` detects the transport and routes around
LiteLLM entirely — no `api_base`, no `api_key`, no `response_format` (a flag the
CLI has no equivalent for).

---

## Tests

`tests/test_provider_registry.py` covers resolution order, per-role override,
unknown-provider handling, explicit-key enforcement, transport-specific kwargs,
and:

> `test_cli_provider_takes_the_bare_tag` — `openai/big-pickle` is not a model
> the CLI recognises; it does its own prefixing.

`tests/test_cli_provider.py` covers event parsing, prompt flattening and the
container-specific remedy. `tests/test_llm_diagnosis.py` covers turning a
provider error into actionable advice.

> `test_all_defaults_are_free` — the one promise that would quietly cost the
> user money. Every shipped default must be in the verified free catalogue.