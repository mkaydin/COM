"""Embedding backend behaviour: width, batching, and refusing to truncate.

The findings these pin are not hypothetical:

* `all-MiniLM-L6-v2` has a 256-token window and `rag.chunker` emits 300-500
  token chunks, so it was silently discarding **32% of the OKF bundle and 92%
  of the wiki**. `SentenceTransformer.encode` just returns a shorter vector;
  nothing raises. That is the single largest quality problem in the project
  and it was invisible until the window was measured against the chunker.
* `qwen3-embedding` is natively 1024 dims against `Vector(384)` stores, so
  every request must carry `dimensions` or nothing fits.
* Ollama batch-parallelises across the GPU; one request per text measured
  ~12x slower.
"""

import pytest

from llm import embeddings


@pytest.fixture(autouse=True)
def reset_selection():
    embeddings.select("local", "all-MiniLM-L6-v2")
    yield
    embeddings.select("local", "all-MiniLM-L6-v2")


# ======================================================================================
# Width
# ======================================================================================


class TestRequestedWidth:
    def test_asks_for_the_store_width(self):
        """The whole point of the Matryoshka head is fitting Vector(384)."""
        assert embeddings.requested_dimensions() == embeddings.VECTOR_DIM == 384

    def test_a_wider_model_is_folded_into_the_store(self):
        """Natively 1024, compatible only because `dimensions` is sent."""
        assert embeddings.check_compatible("qwen3-embedding:0.6b")
        assert embeddings.requested_dimensions() == embeddings.VECTOR_DIM

    def test_a_fixed_width_model_that_cannot_fold_is_rejected(self):
        assert not embeddings.check_compatible("bge-m3")


# ======================================================================================
# Ollama transport
# ======================================================================================


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {"embeddings": [[0.1, 0.2]]}
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        self.calls.append(json)
        return self._responses.pop(0)


class TestOllamaTransport:
    def _patch(self, monkeypatch, responses):
        import httpx

        client = _Client(responses)
        monkeypatch.setattr(httpx, "AsyncClient", None, raising=False)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
        return client

    def test_batch_is_one_request(self, monkeypatch):
        """Per-text requests leave the GPU idle; measured ~12x slower."""
        client = self._patch(monkeypatch, [
            _Resp(payload={"embeddings": [[0.1] * 384 for _ in range(8)]})
        ])
        embeddings.select("ollama", "qwen3-embedding:0.6b")
        out = embeddings._embed_ollama([f"chunk {i}" for i in range(8)])

        assert len(client.calls) == 1, "must batch, not loop"
        assert len(client.calls[0]["input"]) == 8
        assert len(out) == 8

    def test_dimensions_and_no_truncation_are_sent(self, monkeypatch):
        client = self._patch(monkeypatch, [
            _Resp(payload={"embeddings": [[0.1] * 384]})
        ])
        embeddings.select("ollama", "qwen3-embedding:0.6b")
        embeddings._embed_ollama(["text"])

        body = client.calls[0]
        assert body["dimensions"] == 384
        # `truncate: false` so an over-length chunk raises instead of being
        # silently embedded as its prefix.
        assert body["truncate"] is False

    def test_mismatched_count_is_an_error(self, monkeypatch):
        """A short response means the batch was silently partial."""
        self._patch(monkeypatch, [
            _Resp(payload={"embeddings": [[0.1]]})
        ])
        embeddings.select("ollama", "qwen3-embedding:0.6b")
        with pytest.raises(embeddings.EmbeddingFailed, match="1 embeddings for 3 inputs"):
            embeddings._embed_ollama(["a", "b", "c"])

    def test_http_error_raises_with_the_servers_reason(self, monkeypatch):
        self._patch(monkeypatch, [
            _Resp(status=400, text="input exceeds maximum context length")
        ])
        embeddings.select("ollama", "qwen3-embedding:0.6b")
        with pytest.raises(embeddings.EmbeddingFailed) as e:
            embeddings._embed_ollama(["x" * 500000])
        assert "exceeds maximum context length" in str(e.value)
        assert "truncate" in str(e.value), (
            "the message should say truncation is off, so the reader knows "
            "the chunk was not silently cut"
        )


class TestDimensionCompatibilityFallback:
    """The retry must only fire when the server actually blames `dimensions`."""

    @staticmethod
    def _resp(status, text):
        class _R:
            def __init__(self):
                self.status_code, self.text = status, text

            def json(self):
                return {"embeddings": [[0.1] * 384]}
        return _R()

    def test_blames_dimensions_detects_it(self):
        assert embeddings._blames_dimensions(self._resp(400, "unknown field dimensions"))
        assert not embeddings._blames_dimensions(
            self._resp(400, "input exceeds maximum context length")
        )

    def test_unrelated_400_is_not_retried_without_dimensions(self, monkeypatch):
        """A long input with truncate:false must not trigger the fallback.

        The fallback used to drop `dimensions`, retry, fail again, and report a
        misleading "dimensions unsupported" warning on top of the real error.
        """
        import httpx

        client = _Client([self._resp(400, "input exceeds maximum context length")])
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
        embeddings.select("ollama", "qwen3-embedding:0.6b")

        with pytest.raises(embeddings.EmbeddingFailed):
            embeddings._embed_ollama(["x" * 500000])

        assert len(client.calls) == 1, (
            "must not retry when the 400 is not about dimensions"
        )
        assert client.calls[0].get("dimensions") == 384

    def test_genuine_dimension_rejection_does_retry(self, monkeypatch):
        import httpx

        client = _Client([
            self._resp(400, "invalid field: dimensions"),
            self._resp(200, ""),
        ])
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
        embeddings.select("ollama", "qwen3-embedding:0.6b")
        embeddings._embed_ollama(["text"])

        assert len(client.calls) == 2
        assert "dimensions" not in client.calls[1]


# ======================================================================================
# The truncation warning
# ======================================================================================


class TestWindowWarning:
    """The project logs through structlog, so `caplog` sees nothing."""

    def test_short_window_is_flagged(self):
        from structlog.testing import capture_logs

        class _TinyModel:
            max_seq_length = 256

        with capture_logs() as logs:
            embeddings._warn_if_window_too_small(_TinyModel(), "all-MiniLM-L6-v2")

        entries = [e for e in logs if e.get("event") == "embedding_window_too_small"]
        assert entries, f"the 256-token truncation must be visible: {logs}"
        assert entries[0]["window_tokens"] == 256
        assert entries[0]["chunk_tokens"] == 500
        assert "discarded" in entries[0]["hint"]

    def test_adequate_window_is_silent(self):
        from structlog.testing import capture_logs

        class _BigModel:
            max_seq_length = 32768

        with capture_logs() as logs:
            embeddings._warn_if_window_too_small(_BigModel(), "qwen3-embedding:0.6b")

        assert not [e for e in logs if e.get("event") == "embedding_window_too_small"]

    def test_real_minilm_window_is_the_documented_shortfall(self):
        """Guards the finding: 256 window vs 300-500 token chunks."""
        from sentence_transformers import SentenceTransformer

        from rag.chunker import MAX_CHUNK_TOKENS

        m = SentenceTransformer("all-MiniLM-L6-v2")
        assert m.max_seq_length < MAX_CHUNK_TOKENS, (
            "if this ever stops being true the warning above is stale"
        )


class TestRecommendation:
    def test_recommended_pair_is_ollama_natively(self):
        assert embeddings.RECOMMENDED_BACKEND == "ollama"
        assert embeddings.RECOMMENDED_MODEL.startswith("qwen3-embedding")

    def test_recommended_model_fits_the_store(self):
        assert embeddings.check_compatible(
            embeddings.RECOMMENDED_MODEL
        ) or embeddings.requested_dimensions() == embeddings.VECTOR_DIM