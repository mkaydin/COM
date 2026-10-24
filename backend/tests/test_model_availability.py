"""A configured model that the provider no longer offers.

This is the failure that reads as healthy: the key is valid, the endpoint
answers, and the character produces nothing. It has happened twice — first with
`ling-3.1-flash-free` and `step-5-preview-free`, then with
`longcat-2.5-preview-free`, which was the Oracle's default.

`llm.health` now compares every role's model against the provider's live
catalogue and reports `model_present` per role, `missing_models` overall, and a
hint naming what to do.
"""

import pytest

from llm import discovery, health, providers
from llm import _bare_model


@pytest.fixture(autouse=True)
def _dummy_credentials(monkeypatch):
    from config import get_settings

    monkeypatch.setattr(
        get_settings(), "opencode_api_key", "test-key-not-real", raising=False
    )


@pytest.fixture(autouse=True)
def _clear_catalogue_cache():
    discovery._catalogue_cache.clear()
    yield
    discovery._catalogue_cache.clear()


def _cli(models):
    async def fake():
        return {"reachable": True, "models": models, "hint": None, "counts": {}}
    return fake


class TestBareModel:
    @pytest.mark.parametrize("raw,expected", [
        ("deepseek-v4.1-flash", "deepseek-v4.1-flash"),
        ("opencode/deepseek-v4.1-flash", "deepseek-v4.1-flash"),
        ("opencode-go/deepseek-v4.1-flash", "deepseek-v4.1-flash"),
        ("openai/text-embedding-3-small", "text-embedding-3-small"),
        ("ollama/qwen3", "qwen3"),
    ])
    def test_prefixes_are_stripped(self, raw, expected):
        assert _bare_model(raw) == expected


class TestLiveCatalogue:
    async def test_go_is_filtered_to_its_own_models(self, monkeypatch):
        monkeypatch.setattr(discovery, "list_cli_models", _cli([
            "opencode/big-pickle",
            "opencode-go/deepseek-v4.1-flash",
            "opencode-go/glm-5.3",
            "openrouter/llama",
        ]))
        assert await discovery.live_catalogue("opencode-go") == {
            "deepseek-v4.1-flash", "glm-5.3",
        }

    async def test_zen_is_filtered_separately(self, monkeypatch):
        monkeypatch.setattr(discovery, "list_cli_models", _cli([
            "opencode/big-pickle",
            "opencode-go/deepseek-v4.1-flash",
        ]))
        assert await discovery.live_catalogue("opencode") == {"big-pickle"}

    async def test_an_unreachable_listing_is_unknown_not_empty(self, monkeypatch):
        """An empty set would mark every role broken; None says "could not check"."""
        async def unreachable():
            return {"reachable": False, "models": [], "hint": "no CLI", "counts": {}}

        monkeypatch.setattr(discovery, "list_cli_models", unreachable)
        assert await discovery.live_catalogue("opencode-go") is None

    async def test_unknown_provider_is_not_checked(self):
        assert await discovery.live_catalogue("lmstudio") is None

    async def test_the_listing_is_cached(self, monkeypatch):
        calls = []

        async def counting():
            calls.append(1)
            return {"reachable": True, "models": ["opencode-go/x"], "hint": None,
                    "counts": {}}

        monkeypatch.setattr(discovery, "list_cli_models", counting)
        await discovery.live_catalogue("opencode-go")
        await discovery.live_catalogue("opencode-go")
        assert len(calls) == 1, "the catalogue was fetched twice inside the TTL"


def _roles(models: dict[str, str]):
    return [
        {
            "role": role, "model": model, "provider": "opencode-go",
            "provider_error": None, "routed_model": model,
        }
        for role, model in models.items()
    ]


class TestHealthReportsMissingModels:
    async def test_a_delisted_model_makes_health_fail(self, monkeypatch):
        from llm import roles

        monkeypatch.setattr(
            roles, "all_roles",
            lambda: _roles({"ORACLE": "longcat-2.5-preview-free",
                            "SAGE": "mimo-v2.6-flash-free"}),
        )

        async def catalogue(pid, **kw):
            return {"mimo-v2.6-flash-free"}

        monkeypatch.setattr(discovery, "live_catalogue", catalogue)
        detail = await health()

        assert detail["ok"] is False
        assert detail["missing_models"] == ["ORACLE"]
        assert "ORACLE" in (detail["model_hint"] or ""), (
            "the hint must name the affected role, or the operator has to guess"
        )
        per_role = {r["role"]: r["model_present"] for r in detail["roles"]}
        assert per_role == {"ORACLE": False, "SAGE": True}

    async def test_all_models_present_is_healthy(self, monkeypatch):
        from llm import roles

        monkeypatch.setattr(
            roles, "all_roles", lambda: _roles({"ORACLE": "deepseek-v4.1-flash"})
        )

        async def catalogue(pid, **kw):
            return {"deepseek-v4.1-flash"}

        monkeypatch.setattr(discovery, "live_catalogue", catalogue)
        detail = await health()
        assert detail["ok"] is True
        assert detail["missing_models"] == []
        assert detail["model_hint"] is None

    async def test_an_unavailable_catalogue_does_not_break_health(self, monkeypatch):
        """The CLI being absent must not read as "every model is delisted"."""
        from llm import roles

        monkeypatch.setattr(
            roles, "all_roles", lambda: _roles({"ORACLE": "deepseek-v4.1-flash"})
        )

        async def unavailable(pid, **kw):
            return None

        monkeypatch.setattr(discovery, "live_catalogue", unavailable)
        detail = await health()
        assert detail["ok"] is True
        assert detail["roles"][0]["model_present"] is None
        assert detail["missing_models"] == []

    async def test_a_role_provider_error_still_fails_health(self, monkeypatch):
        """Regression: the final `detail['ok'] = ok` used to drop this."""
        from llm import roles

        broken = _roles({"ORACLE": "deepseek-v4.1-flash"})
        broken[0]["provider_error"] = "unknown provider 'nope'"
        monkeypatch.setattr(roles, "all_roles", lambda: broken)

        async def catalogue(pid, **kw):
            return {"deepseek-v4.1-flash"}

        monkeypatch.setattr(discovery, "live_catalogue", catalogue)
        detail = await health()
        assert detail["ok"] is False
        assert detail["unhealthy_roles"] == ["ORACLE"]