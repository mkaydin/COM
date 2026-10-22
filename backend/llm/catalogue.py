"""Model catalogue per provider.

The Council's defaults are **free-tier models**. The list below was verified
against the live Zen catalogue on 2026-10-13 (`GET https://opencode.ai/zen/v1/models`
returned 78 models, 11 of them free). It is a *snapshot*, not a guarantee —
Zen rotates models, and a tag can disappear between releases. Every entry here
is therefore only a **default**: `LLM_MODEL_<ROLE>` overrides it, and the
runtime falls back to `LLM_MODEL_DEFAULT` when a configured model is not in
the live catalogue.

Why each free model was picked (all served on `/v1/chat/completions`, which is
what LiteLLM's `openai/` prefix speaks):

* ``big-pickle``            — general workhorse; the Moderator's job is
                               structured synthesis + JSON, not depth.
* ``minimax-m3.1-flash-free`` — the Oracle's grounded reasoning; replaced
                               ``longcat-2.5-preview-free``, which left the
                               catalogue (see below).
* ``nemotron-3.5-lightning-free`` — fast, retrieval-heavy; the Scholar makes
                               many calls with large grounded context.
* ``mimo-v2.6-flash-free``  — used at high temperature for the Sage's analogy
                               and ethics work.
* ``glm-5.3-flash-free`` / ``deepseek-v4.1-flash-free`` — reserve cast for the
  Analyst and Historian, and for spreading rate-limit pressure across distinct
  endpoints.

**This file rots, and it has rotted twice.** ``ling-3.1-flash-free`` and
``step-5-preview-free`` rotated out first; then ``longcat-2.5-preview-free``
and ``space-bunny-free`` went too, leaving two characters unable to answer at
all because the recorded default was no longer served. Both times the failure
was silent until a run produced nothing. The free tier turns over, so
`GET /api/llm/discovery` is the authority on what exists *now* and this file is
only the curated snapshot — re-verify it against the live catalogue before
trusting it, and prefer the discovery endpoint when choosing models in the UI.

Last verified against the live catalogue on **2026-10-22**: 49 models served,
9 marked ``-free``, plus ``big-pickle``. Every free model below was exercised
through ``opencode-cli`` on that date. Two observations worth keeping:
``jev-1.13-free`` is listed but is not a chat model and fails, and
``flint-40b-free`` answers correctly but took 82s, so it is unsuitable as a
default. ``beam-free`` (0.7s) and ``deepseek-v4.1-flash-free`` (1.9s) are the
fastest.

Deliberately excluded from the chat-completions defaults:

* ``muse-spark-1.2/1.3-contributor-free`` — free, but served on
  ``/v1/responses`` rather than ``/v1/chat/completions``. Usable, but only via
  a provider-specific code path that does not exist yet.
* ``jev-1.13-free`` — the bespoke ``/v1/systemone`` endpoint, not a chat model.
* ``exo-free`` — appeared in the OpenCode model catalog but was absent from the
  live ``/v1/models`` response. Treated as transient; never a default.
"""

# --- opencode (OpenCode Zen) -------------------------------------------------------

OPENCODE_DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"

# --- opencode-go (Go subscription) -------------------------------------------------

#: From `https://models.opencode.ai/api.json` -> `providers["opencode-go"].api`.
#: Same credential as Zen, but the request also needs `x-opencode-session`;
#: without it the endpoint answers 400 and refuses to route.
OPENCODE_GO_DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"

#: `model id -> default role`. One model per role keeps load spread across
#: distinct endpoints, which matters because every one of these is free-tier.
OPENCODE_ROLE_DEFAULTS: dict[str, str] = {
    "MODERATOR": "big-pickle",
    "ORACLE": "minimax-m3.1-flash-free",
    "SCHOLAR": "nemotron-3.5-lightning-free",
    "SAGE": "mimo-v2.6-flash-free",
    "ANALYST": "glm-5.3-flash-free",
    "HISTORIAN": "deepseek-v4.1-flash-free",
}

#: `deepseek-v4.1-flash` for every role.
#:
#: This differs from the Zen table on purpose. The Zen defaults exist to spread
#: free-tier *rate-limits* across distinct models, which has no analogue on a
#: paid subscription. Giving every character the same strong model keeps the
#: conversation comparable between runs — the cast still differs by knowledge
#: source and instructions, which is what makes their answers differ.
#: Override per role with `LLM_MODEL_<ROLE>` or from `/models.html`.
OPENCODE_GO_ROLE_DEFAULTS: dict[str, str] = {
    role: "deepseek-v4.1-flash" for role in OPENCODE_ROLE_DEFAULTS
}

#: Verified free models usable over `/v1/chat/completions`.
OPENCODE_FREE_MODELS: dict[str, dict] = {
    "big-pickle": {
        "name": "Big Pickle",
        "roles": ["MODERATOR"],
        "notes": "general default",
    },
    "minimax-m3.1-flash-free": {
        "name": "MiniMax M3.1 Flash Free",
        "roles": ["ORACLE"],
        "notes": "grounded reasoning over concept bodies",
    },
    "nemotron-3.5-lightning-free": {
        "name": "Nemotron 3.5 Lightning Free",
        "roles": ["SCHOLAR"],
        "notes": "fast, retrieval-heavy",
    },
    "mimo-v2.6-flash-free": {
        "name": "MiMo-V2.6-Flash Free",
        "roles": ["SAGE"],
        "notes": "high-temperature work",
    },
    "glm-5.3-flash-free": {"name": "GLM 5.3 Flash Free", "roles": ["ANALYST"]},
    "deepseek-v4.1-flash-free": {"name": "DeepSeek V4.1 Flash Free", "roles": ["HISTORIAN"]},
    # Verified but unassigned — spare endpoints, useful for spreading
    # rate-limit pressure when a role is repointed from the GUI.
    "beam-free": {"name": "Beam Free", "roles": [], "notes": "fastest measured (0.7s)"},
    "nemotron-3.5-super-vl-free": {"name": "Nemotron 3.5 Super VL Free", "roles": []},
    "flint-40b-free": {
        "name": "Flint 40B Free",
        "roles": [],
        "notes": "correct but ~82s per call; not a default",
    },
}

#: Free models that exist but are not reachable over `/v1/chat/completions`.
#: Listed so the UI can explain *why* they are not offered rather than hiding them.
OPENCODE_FREE_UNSUPPORTED: dict[str, str] = {
    "jev-1.13-free": (
        "listed as free but is not a chat model — fails through the CLI too"
    ),
}

#: Paid models, offered as opt-in upgrades. Never a default.
#: Re-checked against the live catalogue on 2026-10-22; the previous list had
#: rotted the same way the free one had (`claude-sonnet-4-6`, `claude-opus-4-8`
#: and `gemini-3.5-flash` were all gone).
OPENCODE_PAID_SUGGESTIONS: list[str] = [
    "claude-sonnet-5",
    "claude-opus-5-5",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gemini-3.8-flash",
    "deepseek-v4-pro",
    "glm-5.3",
    "kimi-k3",
]

OPENCODE_SNAPSHOT_DATE = "2026-10-22"


# --- ollama (optional local provider) ---------------------------------------------

#: Ollama serves an OpenAI-compatible surface, so no per-role catalogue is
#: needed — the operator sets `LLM_MODEL_<ROLE>` to whatever tag they pulled.
#: This is the fallback when nothing per-role is configured.
OLLAMA_FALLBACK_MODEL = "gemma4:e4b"

#: `model id -> default role` for the optional local provider. Kept separate
#: from the Zen defaults so switching providers never silently re-points a
#: character at a remote model.
OLLAMA_ROLE_DEFAULTS: dict[str, str] = {
    role: OLLAMA_FALLBACK_MODEL for role in OPENCODE_ROLE_DEFAULTS
}


#: Both OpenCode transports reach the same Zen models — `opencode` over HTTP
#: and `opencode-cli` by shelling out to the client. They differ only in how
#: the request travels, so they share one catalogue and one set of defaults.
OPENCODE_PROVIDERS = ("opencode", "opencode-cli")


def uses_opencode_catalogue(provider: str) -> bool:
    """True when `provider` is served by Zen's free-model catalogue."""
    return (provider or "").strip() in OPENCODE_PROVIDERS


def free_models_for(provider: str) -> list[dict]:
    """The verified free-model list for a provider, for the setup UI.

    This used to match only ``"opencode"`` exactly, so under the shipped
    default ``LLM_PROVIDER=opencode-cli`` the setup UI listed *no* free models
    at all — even though the CLI is precisely how you reach them.

    That default is now ``opencode-go``, which has no free tier, so this list
    is naturally empty there; `llm.list_models` substitutes the Go role table.
    """
    if not uses_opencode_catalogue(provider):
        return []
    out = []
    for model_id, meta in OPENCODE_FREE_MODELS.items():
        out.append(
            {
                "id": model_id,
                "name": meta.get("name", model_id),
                "free": True,
                "roles": meta.get("roles", []),
                "notes": meta.get("notes", ""),
            }
        )
    return out


def unsupported_free_models() -> list[dict]:
    """Free models that exist but are not offered, and why."""
    return [
        {"id": k, "free": True, "available": False, "reason": v}
        for k, v in OPENCODE_FREE_UNSUPPORTED.items()
    ]


def default_for_role(role: str, provider: str) -> str:
    """The built-in default model for a role under a provider.

    `role` is matched case-insensitively so `oracle`, `ORACLE` and `Oracle`
    all resolve to the same character.
    """
    key = (role or "").strip().upper()
    table = (
        OPENCODE_ROLE_DEFAULTS
        if uses_opencode_catalogue(provider)
        else OLLAMA_ROLE_DEFAULTS
    )
    if key in table:
        return table[key]
    # Unknown role (a new character added without a default) still gets a
    # usable model rather than an empty string.
    return next(iter(table.values()))