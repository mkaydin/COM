import pytest
from httpx import AsyncClient, ASGITransport
from agents import registry
from main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # "ok" when Postgres is unreachable is the wrong bar — what matters is that
    # the provider block is present and the endpoint does not 500.
    assert data["status"] in ("ok", "degraded")
    assert "provider" in data
    assert "roles" in data


@pytest.mark.asyncio
async def test_agents_list(client):
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)

    # Every registered character, plus the Moderator. Built from the registry
    # rather than hardcoded, so a new character does not need a test edit.
    expected = set(registry.keys()) | {"moderator"}
    assert {a["name"] for a in data} == expected


@pytest.mark.asyncio
async def test_agents_expose_model_binding(client):
    """Each character card must say which model it speaks on.

    This is the whole point of the provider layer: five characters, five
    possibly-different models, visible from the API.
    """
    resp = await client.get("/api/agents")
    data = resp.json()
    for agent in data:
        assert "model" in agent, f"{agent['name']} has no model binding"
        assert "model_role" in agent


@pytest.mark.asyncio
async def test_agent_by_name(client):
    resp = await client.get("/api/agents/oracle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "oracle"


@pytest.mark.asyncio
async def test_agent_not_found(client):
    resp = await client.get("/api/agents/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_forum_status(client):
    resp = await client.get("/api/forum/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert set(data["cast"]) == set(registry.keys())


@pytest.mark.asyncio
async def test_llm_providers_listed(client):
    resp = await client.get("/api/llm/providers")
    assert resp.status_code == 200
    data = resp.json()
    ids = {p["id"] for p in data["providers"]}
    # opencode is the default; ollama is the optional local provider.
    assert {"opencode", "ollama"} <= ids
    assert data["active"] in ids


@pytest.mark.asyncio
async def test_llm_roles_resolved(client):
    """Every role must resolve to a model — no empty strings."""
    resp = await client.get("/api/llm/roles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"]
    for entry in data["roles"]:
        assert entry["model"], f"role {entry['role']} resolved to no model"
        assert entry["routed_model"].startswith("openai/") or entry["routed_model"]


@pytest.mark.asyncio
async def test_llm_free_models_are_free(client):
    """The shipped defaults must be free-tier.

    Guards the one promise that would quietly cost the user money: a default
    silently changing to a paid model.
    """
    from llm import catalogue

    for model_id in catalogue.OPENCODE_ROLE_DEFAULTS.values():
        assert model_id in catalogue.OPENCODE_FREE_MODELS, (
            f"default model {model_id!r} is not in the verified free catalogue"
        )