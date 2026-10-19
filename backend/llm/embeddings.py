"""Embedding model selection.

Three places load a sentence-transformer (`rag/ingest.py`, `okf/bundle.py`,
`ingestion/embedder.py`), each with its own hardcoded
`SentenceTransformer("all-MiniLM-L6-v2")`. That was three copies of the same
model in RAM and no way to change it. This module is the single place a
vector is produced, with the backend and model selectable at runtime.

Backends:

* ``local`` — sentence-transformers. The default, because it needs no server
  and has no per-call network cost.
* ``ollama`` — local embedding models (`nomic-embed-text`, `mxbai-embed-large`).
* ``opencode-cli`` / ``opencode`` — Zen's embedding models
  (`openai/text-embedding-3-small`, `nvidia/nv-embed-v1`, …).

**The vector stores are 384-dimensional.** `rag_chunks.embedding` is
`Vector(384)` and the Chroma collection is created at 384. Choosing an embedder
of a different width makes cosine distance meaningless — retrieval silently
returns noise, it does not raise. `check_compatible` exists so the UI can say
so before the user picks one, and `dimension()` reports the configured width
so callers can refuse rather than corrupt a collection.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)

#: Backends the picker offers. `local` first: it is the fallback and the only
#: one that needs nothing else running.
BACKENDS = ("local", "ollama", "opencode-cli", "opencode")

#: The recommended default. `qwen3-embedding:0.6b` is Ollama-native, ~62
#: chunks/s on one GPU, has a 32k window (so nothing is truncated), and its
#: Matryoshka head can emit exactly `VECTOR_DIM` dimensions — meaning it fits
#: the existing 384-wide stores with no migration. `all-MiniLM-L6-v2` is
#: roughly twice as fast per batch but silently drops everything past 256
#: tokens, which was most of this project's own knowledge base.
RECOMMENDED_BACKEND = "ollama"
RECOMMENDED_MODEL = "qwen3-embedding:0.6b"

#: Width of every vector store in this project. See the module docstring.
VECTOR_DIM = 384

#: Models natively 384-dim, so they need no folding.
_KNOWN_384 = ("all-minilm-l6-v2",)

#: Models that are *not* 384 but can be asked for it, because the backend sends
#: `dimensions` and the model was Matryoshka-trained to keep its leading
#: dimensions meaningful. `qwen3-embedding` is natively 1024 and lands in the
#: existing stores without a migration.
_FOLDABLE = ("qwen3-embedding", "text-embedding-3")

_st_model = None
_st_model_name: Optional[str] = None
_backend: Optional[str] = None
_model: Optional[str] = None

#: Ollama can batch an entire list in one request; a per-text loop leaves the
#: GPU idle. Measured ~12x slower one-at-a-time on an RTX 4060.
_EMBED_TIMEOUT = 300.0


def requested_dimensions() -> Optional[int]:
    """The output width to ask for, or None to take the model's native width.

    Only ever `VECTOR_DIM` today — the point is to fit a wider model
    (`qwen3-embedding` is natively 1024) into the existing 384-wide stores
    without a schema migration. Matryoshka truncation is lossy but principled:
    the model was trained to keep its leading dimensions meaningful.
    """
    return VECTOR_DIM


def _blames_dimensions(resp) -> bool:
    """Does this 400 actually complain about the `dimensions` field?

    Guards the compatibility retry. Retrying every 400 hid the real cause of
    the failure behind a misleading warning.
    """
    try:
        detail = resp.text.lower()
    except Exception:
        return False
    return "dimension" in detail


class EmbeddingFailed(RuntimeError):
    """The embedding model could not serve the request.

    Distinct from a transport error so callers can tell "the model said no"
    from "Ollama is not running".
    """


class EmbeddingBackendMismatch(RuntimeError):
    """Something reached for the local model while a remote backend is active."""


def configured_backend() -> str:
    """The embedding backend currently selected."""
    from llm import discovery

    configured = _backend or getattr(get_settings(), "embedding_backend", "local") or "local"
    return configured if configured in BACKENDS else "local"


def configured_model() -> str:
    """The embedding model currently selected."""
    from llm import discovery

    return _model or getattr(get_settings(), "embedding_model", "") or discovery.DEFAULT_LOCAL_EMBEDDING


def current() -> dict:
    """The active selection, for `/api/llm/embedding` and the UI."""
    from llm import discovery

    backend = configured_backend()
    model = configured_model()
    return {
        "backend": backend,
        "model": model,
        "dimension": VECTOR_DIM,
        "compatible": check_compatible(model),
        "source": "runtime" if _model or _backend else "env",
        "hint": None,
        "supported": list(BACKENDS),
        "default_model": discovery.DEFAULT_LOCAL_EMBEDDING,
    }


def check_compatible(model: str) -> bool:
    """Can this model produce vectors the existing stores can hold?

    Three ways to be compatible: natively `VECTOR_DIM` wide, or able to be
    *folded* to it (`dimensions` is sent and the model was Matryoshka-trained),
    or unknown-but-plausible. Only models known to be a fixed width that is not
    ours are rejected.
    """
    name = (model or "").lower().rsplit("/", 1)[-1]
    if any(k in name for k in _KNOWN_384):
        return True
    if any(k in name for k in _FOLDABLE):
        return True
    # Fixed-width models that are not ours, with no folding support.
    if "bge-m3" in name or "nv-embed" in name:
        return False
    if "text-embedding-ada-002" in name:
        return False
    # Unrecognised: permissive, because refusing to let the user pick would be
    # worse than a warning. `/api/llm/embedding/test` reports the real width.
    return True


def select(backend: str, model: str) -> dict:
    """Set the embedding backend and model for this process.

    Applies immediately: the cached local model is dropped so the next call
    loads the new one. Not persisted — that is the settings file's job, and
    doing it silently from a UI click would edit the user's `.env` behind them.
    """
    global _backend, _model, _st_model, _st_model_name

    if backend and backend not in BACKENDS:
        raise ValueError(
            f"Unknown embedding backend {backend!r}. Known: {', '.join(BACKENDS)}"
        )
    if not model:
        raise ValueError("An embedding model id is required")

    reset_local_cache()
    _backend = backend or None
    _model = model
    logger.info(
        "embedding_model_selected",
        backend=_backend or "local",
        model=model,
        compatible=check_compatible(model),
    )
    return current()


def reset_local_cache() -> None:
    """Drop the loaded sentence-transformer so a new selection takes effect."""
    global _st_model, _st_model_name
    _st_model = None
    _st_model_name = None


def _get_local_model():
    """The loaded sentence-transformer, reloading if the selection changed.

    Only valid for the `local` backend. Asking for the model object when a
    remote backend is selected used to hand an Ollama tag to
    `SentenceTransformer()`, which then failed deep inside HuggingFace with a
    repo-id validation error — three frames from the actual cause.
    """
    global _st_model, _st_model_name

    backend = configured_backend()
    if backend != "local":
        raise EmbeddingBackendMismatch(
            f"The embedding backend is {backend!r}, so there is no local "
            f"sentence-transformer to return (model {configured_model()!r}). "
            "Use `embed()` / `embed_one()`, which route to the selected backend."
        )

    name = configured_model()
    if _st_model is None or _st_model_name != name:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(name)
        _st_model_name = name
        logger.info("embedding_local_model_loaded", model=name)
        _warn_if_window_too_small(_st_model, name)
    return _st_model


def _warn_if_window_too_small(model, name: str) -> None:
    """Warn when the model's window is shorter than the chunks we produce.

    `all-MiniLM-L6-v2` has a 256-token window and `rag.chunker` emits 300-500
    token chunks, so it was discarding 32% of the OKF bundle and 92% of the
    wiki — silently, because the encode just returns a shorter vector. That is
    the single largest quality problem in this project and it was invisible.
    """
    window = getattr(model, "max_seq_length", None)
    if not window:
        return
    try:
        from rag.chunker import MAX_CHUNK_TOKENS
    except Exception:
        return
    if window < MAX_CHUNK_TOKENS:
        logger.warning(
            "embedding_window_too_small",
            model=name,
            window_tokens=int(window),
            chunk_tokens=MAX_CHUNK_TOKENS,
            hint=(
                f"{name} accepts {int(window)} tokens but chunks run up to "
                f"{MAX_CHUNK_TOKENS}; text past {int(window)} is discarded "
                "without warning. Use a model with a longer window "
                "(qwen3-embedding has 32k) or lower the chunk size."
            ),
        )


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Blocking embed. Always called through `embed` (off the event loop)."""
    backend = configured_backend()

    if backend == "local":
        model = _get_local_model()
        vectors = model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vectors]

    if backend == "ollama":
        return _embed_ollama(texts)

    return _embed_opencode(texts)


def _embed_ollama(texts: list[str]) -> list[list[float]]:
    """Embed through a local Ollama server's `/api/embed`.

    Sends the whole batch in one request: Ollama parallelises it across the GPU,
    whereas one-request-per-text leaves the card mostly idle (measured ~12x
    slower on an RTX 4060).

    `dimensions` is passed so a Matryoshka model (`qwen3-embedding`) can be
    truncated to the vector width the stores use. That is what lets a
    1024-dim model be used against a `Vector(384)` column without a migration.
    """
    import httpx

    s = get_settings()
    url = s.ollama_base_url.rstrip("/") + "/api/embed"
    model = configured_model()
    body: dict = {"model": model, "input": texts, "truncate": False}
    dims = requested_dimensions()
    if dims:
        body["dimensions"] = dims

    logger.debug("embedding_via_ollama", model=model, count=len(texts), dims=dims)

    # `truncate: False` is deliberate: a chunk longer than the model's window
    # should raise rather than be silently embedded as its prefix. That is
    # exactly the failure that made all-MiniLM (256 tokens) quietly discard
    # 32% of the OKF bundle and 92% of the wiki.
    with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
        resp = client.post(url, json=body)
        if resp.status_code == 400 and dims and _blames_dimensions(resp):
            # Older Ollama builds reject the `dimensions` field outright. Retry
            # without it and let the width check downstream report the
            # mismatch.
            #
            # Only on a 400 that *names* the field. A 400 with any other cause
            # (an over-length input with `truncate: false`, say) must not be
            # retried here: that path used to drop `dimensions`, retry, fail
            # again, and surface a misleading "unsupported" warning on top of
            # the real error.
            logger.warning("embedding_dimensions_unsupported", model=model)
            body.pop("dimensions", None)
            resp = client.post(url, json=body)
        if resp.status_code >= 400:
            raise EmbeddingFailed(
                f"Ollama rejected {len(texts)} input(s) for {model!r} "
                f"(HTTP {resp.status_code}): {resp.text[:300]}. "
                "`truncate` is off so nothing is silently cut — check the "
                "chunk length against the model's window."
            )
        payload = resp.json()

    vectors = payload.get("embeddings") or []
    if len(vectors) != len(texts):
        raise EmbeddingFailed(
            f"Ollama returned {len(vectors)} embeddings for {len(texts)} inputs "
            f"from {model!r} — the batch was silently partial"
        )
    return [[float(x) for x in v] for v in vectors]


def _embed_opencode(texts: list[str]) -> list[list[float]]:
    """Embed through Zen's embedding models, via the CLI when available.

    The CLI is the only route to Zen's *free* models, and the embedding models
    are not on the free list, so this normally needs a funded account.
    """
    from llm import cli_provider, providers

    model = configured_model()
    provider = providers.get("opencode-cli")

    if provider.transport:
        import subprocess

        binary = cli_provider.binary_path()
        if binary is None:
            raise cli_provider.CliNotInstalled(
                "The `opencode` CLI is not on PATH; embedding via Zen needs it."
            )
        # The CLI has no embedding subcommand, so this path is only reachable
        # once a dedicated embed transport exists. Fail with the reason rather
        # than shelling out and sending text to a chat model.
        raise NotImplementedError(
            f"The opencode CLI cannot embed yet ({model!r}). Use the "
            "`opencode` HTTP provider, Ollama, or the local backend."
        )

    import httpx

    s = get_settings()
    key = s.opencode_api_key
    if not key:
        raise providers.MissingCredential(
            "Zen embedding models need OPENCODE_API_KEY. Use the local backend "
            "or Ollama for free embeddings."
        )
    base = (s.opencode_base_url or "").rstrip("/")
    out: list[list[float]] = []
    with httpx.Client(timeout=60.0) as client:
        for text in texts:
            resp = client.post(
                f"{base}/v1/embeddings",
                json={"model": model, "input": text},
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                raise RuntimeError(f"No embedding returned for {model!r}")
            out.append([float(x) for x in data[0]["embedding"]])
    return out


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed off the event loop.

    Every caller does this — the encode is CPU-bound and the first call loads
    the model, so running it inline stalls all concurrent requests.
    """
    if not texts:
        return []
    return await asyncio.to_thread(embed_sync, texts)


async def embed_one(text: str) -> list[float]:
    """Embed a single string."""
    vectors = await embed([text])
    return vectors[0] if vectors else []


def width_of(vectors: list[list[float]]) -> int:
    """Width of a batch of vectors, or 0 if empty."""
    return len(vectors[0]) if vectors else 0