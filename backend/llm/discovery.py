"""Model discovery — what each provider can actually serve, right now.

Three sources, and they answer different questions:

* ``opencode-cli`` — runs `opencode models`, which is the **live** Zen
  catalogue. Several hundred models, including embedding models
  (`openai/text-embedding-3-small`, `nvidia/nv-embed-v1`, …). The static
  `llm/catalogue.py` snapshot is kept as the curated list of *known-good free*
  models, but this is the authority on what exists.
* ``opencode`` — the same catalogue, filtered to what a funded account may use.
* ``ollama`` — `/api/tags` for locally pulled models, with capability detection.

Everything here is best-effort: a provider that cannot be reached yields an
empty list plus a reason, never an exception. A model picker that 500s because
Ollama is down is useless precisely when the user needs it to explain why.

Model ids are **bare** — no LiteLLM prefix. `providers.Provider.model_id()`
does the routing prefix, and doing it in two places is how you end up with
`openai/openai/gpt-5` somewhere.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from config import get_settings
from llm import embeddings, providers
from logging_config import get_logger

logger = get_logger(__name__)

#: How long to wait on a local/CLI probe. Long enough for a cold `opencode
#: models` on a loaded machine, short enough that the settings page still
#: paints when the provider is dead.
DISCOVERY_TIMEOUT = 25.0

#: Substrings that mark a model as an embedding model rather than a chat one.
#: Used to split the picker into "language models" and "embedding models".
#: Ollama reports capability properly; the Zen catalogue has no such metadata,
#: so this is a name heuristic and is documented as such.
_EMBED_HINTS = (
    "embed",
    "bge-",
    "gte-",
    "e5-",
    "minilm",
    "nomic-embed",
    "jina-embed",
    "mxbai-embed",
    "snowflake-arctic-embed",
    "text-embedding",
)

#: The default local embedding model. 384 dims, which is what
#: `rag_chunks.embedding` and the Chroma collection were sized for.
DEFAULT_LOCAL_EMBEDDING = "all-MiniLM-L6-v2"

#: Dimension of DEFAULT_LOCAL_EMBEDDING. Changing it requires a re-embed; the
#: vector column and every Chroma collection are fixed at 384.
EMBEDDING_DIM = 384


def is_embedding_model(model_id: str) -> bool:
    """Heuristic: does this id look like an embedding model?"""
    name = (model_id or "").lower().rsplit("/", 1)[-1]
    return any(h in name for h in _EMBED_HINTS)


def _normalise(line: str) -> Optional[str]:
    line = (line or "").strip()
    return line or None


# ======================================================================================
# opencode CLI — the live Zen catalogue
# ======================================================================================


async def list_cli_models() -> dict:
    """`opencode models` — every model the client can reach.

    Includes embedding models, which is why this exists rather than reusing the
    chat-only `llm/catalogue.py` snapshot.
    """
    from llm import cli_provider

    binary = cli_provider.binary_path()
    if binary is None:
        return {
            "models": [],
            "reachable": False,
            "hint": "The `opencode` CLI is not on PATH, so the live catalogue "
                    "cannot be listed. Free models need it; the pinned models "
                    "below still work.",
        }

    def _run() -> tuple[int, str, str]:
        import subprocess

        proc = subprocess.run(
            [binary, "models"], capture_output=True, text=True,
            timeout=DISCOVERY_TIMEOUT,
        )
        return proc.returncode, proc.stdout, proc.stderr

    try:
        returncode, stdout, stderr = await asyncio.to_thread(_run)
    except asyncio.TimeoutError:
        return {"models": [], "reachable": False,
                "hint": f"`opencode models` timed out after {DISCOVERY_TIMEOUT:.0f}s"}
    except Exception as e:  # pragma: no cover - defensive
        return {"models": [], "reachable": False, "hint": str(e)[:200]}

    if returncode != 0:
        detail = (stderr or stdout or "").strip()[-200:]
        return {"models": [], "reachable": False,
                "hint": f"`opencode models` exited {returncode}: {detail}"}

    models = [m for m in (_normalise(l) for l in stdout.splitlines()) if m]
    free = [m for m in models if m.endswith("-free") or "/free" in m]
    return {
        "models": models,
        "free": free,
        "reachable": True,
        "hint": None,
        "counts": {
            "total": len(models),
            "free": len(free),
            "embedding": sum(1 for m in models if is_embedding_model(m)),
        },
    }


# ======================================================================================
# Ollama — locally pulled models
# ======================================================================================


async def list_ollama_models() -> dict:
    """`/api/tags` plus the `/api/show` capability probe.

    Ollama's tags endpoint does not say whether a model is an embedder, which
    the picker needs in order to keep embedders out of the chat dropdown. It
    exposes `capabilities` on newer servers; where that is absent the model name
    is the fallback signal.
    """
    import httpx

    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
            resp = await client.get(s.ollama_tags_url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.info("ollama_discovery_unavailable", url=s.ollama_tags_url, error=str(e)[:120])
        return {
            "models": [],
            "reachable": False,
            "hint": f"Ollama unreachable at {s.ollama_base_url}",
        }

    entries = []
    for m in payload.get("models", []):
        name = m.get("name") or m.get("model")
        if not name:
            continue
        detail = m.get("details") or {}
        caps = m.get("capabilities") or []
        embedding = (
            "embedding" in caps
            or bool(m.get("embeddings"))
            or is_embedding_model(name)
        )
        entries.append({
            "id": name,
            "size_bytes": detail.get("size"),
            "parameter_size": detail.get("parameter_size"),
            "quantization": detail.get("quantization_level"),
            "family": detail.get("family"),
            "capabilities": caps,
            "embedding": embedding,
            "free": True,
        })

    return {
        "models": entries,
        "reachable": True,
        "hint": None if entries else "No models pulled yet — run: ollama pull <model>",
        "counts": {
            "total": len(entries),
            "embedding": sum(1 for e in entries if e["embedding"]),
        },
    }


# ======================================================================================
# The combined view the UI consumes
# ======================================================================================


#: How long a live catalogue is reused. `/api/health` is polled by the UI and
#: `opencode models` spawns a process, so fetching on every request would be
#: both slow and noisy.
CATALOGUE_TTL = 300.0
_catalogue_cache: dict[str, tuple[float, set[str]]] = {}


async def live_catalogue(
    provider_id: str, *, max_age: float = CATALOGUE_TTL
) -> Optional[set[str]]:
    """Bare model ids this provider currently offers, or None if unknowable.

    Used by `llm.health` to notice that a configured model has been delisted.
    Returns None rather than an empty set when the listing is unavailable:
    "I could not check" and "nothing is offered" are different claims, and
    conflating them would mark every role broken whenever the CLI is missing.
    """
    import time

    pid = (provider_id or "").strip().lower()
    cached = _catalogue_cache.get(pid)
    if cached and (time.monotonic() - cached[0]) < max_age:
        return cached[1]

    if pid == "ollama":
        res = await list_ollama_models()
        if not res.get("reachable"):
            return None
        ids = {m["id"] for m in res.get("models", []) if m.get("id")}
    elif pid in ("opencode", "opencode-cli", "opencode-go"):
        res = await list_cli_models()
        if not res.get("reachable"):
            return None
        # The CLI lists every provider it knows about, prefixed. Only the
        # entries for *this* provider are relevant, and the role stores the
        # bare tag, so the prefix has to come off.
        prefix = "opencode-go/" if pid == "opencode-go" else "opencode/"
        ids = {
            mid[len(prefix):]
            for mid in res.get("models", [])
            if mid.startswith(prefix)
        }
    else:
        return None

    _catalogue_cache[pid] = (time.monotonic(), ids)
    return ids


async def discover(provider_id: Optional[str] = None) -> dict:
    """Everything a model picker needs, for one provider or all of them.

    Shape is stable: every provider gets `models`, `reachable` and `hint`, so
    the frontend never branches on provider identity to find out what to render.
    """
    try:
        pid = (provider_id or get_settings().llm_provider or "opencode-go").lower()
    except Exception:
        pid = "opencode-go"

    out: dict = {
        # The same shape as `GET /api/llm/embedding`, so the page can render the
        # embedding panel from this one response instead of racing a second call.
        "embedding": embeddings.current(),
        "providers": {},
    }

    for candidate in providers.available():
        try:
            out["providers"][candidate] = await _discover_one(candidate)
        except Exception as e:  # pragma: no cover - defensive
            # One broken provider must not blank the whole picker.
            logger.warning("model_discovery_failed", provider=candidate, error=str(e)[:160])
            out["providers"][candidate] = {
                "id": candidate, "models": [], "reachable": False,
                "hint": f"Discovery failed: {str(e)[:160]}",
            }

    out["active"] = pid
    return out


async def _discover_one(pid: str) -> dict:
    provider = providers.get(pid)
    label = provider.label

    if provider.transport == "cli_provider":
        res = await list_cli_models()
        models = [
            {"id": mid, "embedding": is_embedding_model(mid),
             "free": mid.endswith("-free"), "label": mid}
            for mid in res.get("models", [])
        ]
        return {
            "id": pid, "label": label, "reachable": res.get("reachable", False),
            "models": models, "hint": res.get("hint"),
            "counts": res.get("counts"),
            "source": "opencode models (live)",
            "note": provider.notes,
        }

    if pid == "ollama":
        res = await list_ollama_models()
        return {
            "id": pid, "label": label, "reachable": res.get("reachable", False),
            "models": res.get("models", []), "hint": res.get("hint"),
            "counts": res.get("counts"),
            "source": "/api/tags",
            "note": provider.notes,
        }

    if pid == "opencode-go":
        # The Go endpoint serves no `/models` (it 404s), so the catalogue has
        # to come from somewhere else. `opencode models` already lists the Go
        # models as `opencode-go/<id>`, so filter that rather than adding a
        # 6 MB download of `models.opencode.ai/api.json`. Listing through the
        # CLI does not make calls depend on it — only discovery does.
        res = await list_cli_models()
        prefix = "opencode-go/"
        live = [m for m in res.get("models", []) if m.startswith(prefix)]
        if live:
            models = [
                {"id": m[len(prefix):], "embedding": is_embedding_model(m),
                 "free": m.endswith("-free"), "label": m[len(prefix):]}
                for m in live
            ]
            hint = None
            source = "opencode models (live, via the CLI)"
        else:
            # No CLI: still show something selectable rather than an empty
            # picker, and say why it is short.
            defaults = sorted(set(provider.role_defaults.values()))
            models = [
                {"id": m, "embedding": is_embedding_model(m),
                 "free": m.endswith("-free"), "label": m}
                for m in defaults
            ]
            hint = res.get("hint") or (
                "The `opencode` CLI is not on PATH, so only the built-in "
                "defaults are listed."
            )
            # The CLI being absent is a *listing* problem only; say so, or the
            # operator will think the provider itself is unusable.
            hint = f"{hint.rstrip()} Calls still work over HTTP."
            source = "catalogue (static)"
        return {
            "id": pid, "label": label, "reachable": bool(provider.api_key),
            "models": models, "hint": hint,
            "counts": res.get("counts"), "source": source,
            "note": provider.notes,
        }

    # `opencode` over HTTP: the live catalogue is the same list the CLI serves,
    # but a funded account is what makes paid entries usable. Reuse the CLI
    # listing when the binary is there, and say so rather than pretending to
    # have queried an endpoint we do not.
    res = await list_cli_models()
    if res.get("reachable"):
        models = [
            {"id": mid, "embedding": is_embedding_model(mid),
             "free": mid.endswith("-free"), "label": mid}
            for mid in res.get("models", [])
        ]
        hint = None
        source = "opencode models (live, via the CLI)"
    else:
        models = []
        hint = (
            "Set OPENCODE_API_KEY to list Zen models. Without the CLI the "
            "catalogue cannot be enumerated from here."
        )
        source = "unavailable"
    return {
        "id": pid, "label": label, "reachable": bool(
            res.get("reachable") and provider.api_key
        ),
        "models": models, "hint": hint,
        "counts": res.get("counts"), "source": source,
        "note": provider.notes,
    }


def pick_default_models(discovered: dict, provider_id: str) -> list[str]:
    """The models to preselect in a picker: free chat models first.

    Embedders are excluded — an embedder in the chat dropdown is the single
    most confusing state this page can land in.
    """
    entry = (discovered.get("providers") or {}).get(provider_id) or {}
    models = entry.get("models") or []
    chat = [m for m in models if not m.get("embedding")]
    free = [m for m in chat if m.get("free")]
    return [m["id"] for m in (free or chat)]


def known_embedding_models(discovered: dict, provider_id: str) -> list[str]:
    """Embedding models a provider can serve, for the embedding dropdown."""
    entry = (discovered.get("providers") or {}).get(provider_id) or {}
    return [
        m["id"] for m in (entry.get("models") or [])
        if m.get("embedding")
    ]


_DIM_RE = re.compile(r"(\d{3,4})\s*dim", re.IGNORECASE)


def guess_dimension(hint_text: str, fallback: int = EMBEDDING_DIM) -> int:
    """Read a dimension hint out of a model id or free-text note.

    Not authoritative — vector stores are fixed at 384 by schema — but it is
    enough to warn the user in the UI before they pick a mismatched embedder.
    """
    m = _DIM_RE.search(hint_text or "")
    return int(m.group(1)) if m else fallback