# Providers

The Council runs five characters, each potentially on a different model. This
document is how that works and how to change it.

## Providers

| id | default | base URL | auth | notes |
|----|---------|----------|------|-------|
| `opencode` | ✅ | `https://opencode.ai/zen/v1` | `OPENCODE_API_KEY` | OpenAI-compatible `/chat/completions`. |
| `ollama` | optional | `http://$OLLAMA_HOST:$OLLAMA_PORT/v1` | placeholder | Local. The tag must be pulled. |

Both are reached through LiteLLM's `openai/` prefix with an explicit
`api_base` and `api_key`. The environment is never consulted for these — this
project once talked to whatever proxy happened to be exported in the shell.

Adding a provider: one `Provider` in `backend/llm/providers.py` and one branch
in `BUILDERS`. Nothing else changes.

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
# backend/.env, or docker/.env for the compose stack
LLM_PROVIDER=opencode
OPENCODE_API_KEY=...
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

For `model_for("ORACLE")`:

1. a runtime override in the `llm_roles` table (`POST /api/llm/roles`)
2. `LLM_MODEL_ORACLE`
3. the provider's built-in default for that role
4. `LLM_MODEL_DEFAULT`
5. any default for the provider

Overrides live in Postgres, so a choice made in the UI survives a restart.

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
GET    /api/llm/roles              the live role → model mapping + its source
POST   /api/llm/roles              {role, model_id}   persists, takes effect now
DELETE /api/llm/roles/{role}       back to env/default
GET    /api/llm/models             free catalogue + installed local tags
POST   /api/llm/test               {model_id} — send a real completion
POST   /api/llm/reload             re-read persisted overrides
```

`GET /api/llm/roles`:

```jsonc
{
  "provider": "opencode",
  "base_url": "https://opencode.ai/zen/v1",
  "roles": [
    {"role": "MODERATOR", "model": "big-pickle",
     "routed_model": "openai/big-pickle", "source": "default",
     "is_free": true, "character": null, "character_name": null},
    {"role": "ORACLE", "model": "longcat-2.5-preview-free", "…": "…",
     "character": "oracle", "character_name": "The Oracle"}
  ]
}
```

`source` is `runtime` · `env` · `default` · `fallback`. The UI shows it, so you
can always tell why a character is on a given model.

### The UI

`/llm_settings.html` — the mapping, the free catalogue, per-role dropdowns,
and a reset for anything pinned.

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
| no API key | raises before the call — one clear error, not five silent ones |
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

Then add the settings fields and register the catalogue in `llm/catalogue.py`
if it has free models. Nothing else in the codebase changes.

---

## Tests

`tests/test_provider_registry.py` covers resolution order, per-role override,
unknown-provider handling, explicit-key enforcement, and:

> `test_all_defaults_are_free` — the one promise that would quietly cost the
> user money. Every shipped default must be in the verified free catalogue.