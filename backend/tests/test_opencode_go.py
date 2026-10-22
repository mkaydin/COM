"""The Go subscription over plain HTTP.

This replaced the `opencode-cli` default. Two things make the Go endpoint
different from every other provider and are worth pinning separately:

* it lives at `/inference/go/openai/v1`, not `/zen/v1`;
* it refuses to route without `x-opencode-session`, answering
  ``{"error": "Request is missing x-opencode-session and cannot be routed"}``.

The free-tier facts that shaped the old default still hold — free models are
refused outside the OpenCode client — so the CLI provider stays available. It
is just no longer what runs by default.
"""

import os

import pytest

from config import get_settings
from llm import build_kwargs, providers, roles
from llm import discovery

GO_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
MODEL = "deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def _dummy_credentials(request, monkeypatch):
    """A fake key for unit tests; never for the live ones.

    Without the marker check this fixture silently replaces the real credential
    and every live call fails 401 "Invalid credential" — which looks like a
    broken endpoint rather than a broken test.
    """
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(
        get_settings(), "opencode_api_key", "test-key-not-real", raising=False
    )


class TestProviderShape:
    def test_endpoint_is_the_go_path(self):
        assert providers.get("opencode-go").base_url == GO_BASE_URL

    def test_it_speaks_http_not_the_cli(self):
        p = providers.get("opencode-go")
        assert p.transport is None
        assert p.model_id(MODEL) == f"openai/{MODEL}"

    def test_it_needs_a_key(self):
        assert providers.get("opencode-go").requires_key is True

    def test_free_catalogue_is_empty_by_design(self):
        """A subscription has no free tier; the Go role table is the catalogue."""
        from llm import catalogue
        assert catalogue.free_models_for("opencode-go") == []


class TestRoleResolution:
    def test_every_role_uses_the_subscription_model(self):
        p = providers.get("opencode-go")
        for role in roles.KNOWN_ROLES:
            assert roles.model_for(role, p) == MODEL

    def test_the_per_role_override_still_wins(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "llm_model_sage", "glm-5.3", raising=False)
        assert roles.model_for("SAGE", providers.get("opencode-go")) == "glm-5.3"

    def test_switching_provider_does_not_leak_models(self):
        """A Zen model id must not be handed to the Go endpoint."""
        zen = providers.get("opencode")
        go = providers.get("opencode-go")
        assert roles.model_for("ORACLE", zen) != roles.model_for("ORACLE", go)


class TestRequestShape:
    def test_kwargs_carry_the_session_header(self):
        kwargs = build_kwargs(role="ORACLE", messages=[])
        assert kwargs["api_base"] == GO_BASE_URL
        assert kwargs["model"] == f"openai/{MODEL}"
        assert kwargs["extra_headers"]["x-opencode-session"]

    def test_other_providers_do_not_gain_the_header(self):
        """Zen and Ollama requests must not silently change shape."""
        for pid in ("opencode", "opencode-cli", "ollama"):
            kwargs = build_kwargs(role="ORACLE", messages=[], provider=providers.get(pid))
            assert "extra_headers" not in kwargs, pid

    def test_a_caller_can_override_the_session(self, monkeypatch):
        monkeypatch.setattr(
            get_settings(), "opencode_session", "pinned-session", raising=False
        )
        kwargs = build_kwargs(role="ORACLE", messages=[])
        assert kwargs["extra_headers"]["x-opencode-session"] == "pinned-session"

    def test_unknown_provider_still_raises(self):
        with pytest.raises(providers.ProviderError):
            providers.get("opencode-gp")


@pytest.mark.asyncio(loop_scope="session")
class TestDiscovery:
    async def test_filters_the_cli_listing_to_go_models(self, monkeypatch):
        """The Go endpoint has no `/models`; the CLI list carries the ids."""
        async def fake_cli():
            return {
                "reachable": True,
                "models": [
                    "opencode/big-pickle",
                    f"opencode-go/{MODEL}",
                    "opencode-go/glm-5.3",
                    "ollama/qwen3",
                ],
                "hint": None,
                "counts": {"opencode": 2, "opencode-go": 2},
            }

        monkeypatch.setattr(discovery, "list_cli_models", fake_cli)
        out = await discovery._discover_one("opencode-go")

        ids = [m["id"] for m in out["models"]]
        assert ids == [MODEL, "glm-5.3"]
        assert all(not i.startswith("opencode-go/") for i in ids), (
            "the prefix must be stripped — the endpoint expects the bare tag"
        )
        assert out["reachable"] is True

    async def test_without_the_cli_it_still_offers_the_defaults(self, monkeypatch):
        async def no_cli():
            return {"reachable": False, "models": [],
                    "hint": "the CLI is not on PATH", "counts": {}}

        monkeypatch.setattr(discovery, "list_cli_models", no_cli)
        out = await discovery._discover_one("opencode-go")

        assert [m["id"] for m in out["models"]] == [MODEL]
        assert "calls still work over http" in (out["hint"] or "").lower(), (
            "the hint must say the provider is still usable — otherwise an "
            "operator reads 'CLI not on PATH' as 'this provider is broken'"
        )
        assert out["source"] == "catalogue (static)"

    async def test_embedding_models_are_still_flagged(self, monkeypatch):
        async def fake_cli():
            return {"reachable": True,
                    "models": ["opencode-go/nomic-embed-text", f"opencode-go/{MODEL}"],
                    "hint": None, "counts": {}}

        monkeypatch.setattr(discovery, "list_cli_models", fake_cli)
        out = await discovery._discover_one("opencode-go")
        flags = {m["id"]: m["embedding"] for m in out["models"]}
        assert flags["nomic-embed-text"] is True
        assert flags[MODEL] is False


@pytest.mark.live
@pytest.mark.asyncio(loop_scope="session")
class TestLiveSubscription:
    """Real calls. `pytest -m live --run-live`."""

    def _key_present(self) -> bool:
        return bool(os.environ.get("OPENCODE_API_KEY") or get_settings().opencode_api_key)

    async def test_deepseek_flash_answers_over_http(self):
        if not self._key_present():
            pytest.skip("OPENCODE_API_KEY is not set")

        from llm import llm_complete

        resp = await llm_complete(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            role="ORACLE", temperature=0.0, max_tokens=64,
        )
        content = (resp.choices[0].message.content or "").strip()
        assert "OK" in content, f"unexpected reply: {content!r}"

    async def test_the_reported_model_is_the_one_requested(self):
        if not self._key_present():
            pytest.skip("OPENCODE_API_KEY is not set")

        from llm import llm_complete

        resp = await llm_complete(
            [{"role": "user", "content": "Reply with exactly: OK"}], role="ORACLE",
            temperature=0.0, max_tokens=64,
        )
        # `llm_complete` returns the project's `LLMResult` wrapper; the provider's
        # own response sits at `.response`. The wrapper has no `model` attribute,
        # so asking the wrapper would silently pass with `None`.
        reported = getattr(resp.response, "model", None)
        assert reported == MODEL, f"response came from {reported!r}, not {MODEL}"

    async def test_a_bare_request_without_the_header_would_fail(self):
        """Pins the reason the header exists, against the real endpoint.

        If the endpoint ever stops requiring it, this test fails and the
        header can be removed rather than carried as folklore.
        """
        if not self._key_present():
            pytest.skip("OPENCODE_API_KEY is not set")

        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{GO_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {get_settings().opencode_api_key}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 8,
                },
            )
        assert resp.status_code == 400
        assert "x-opencode-session" in resp.text