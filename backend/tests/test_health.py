import pytest
from httpx import AsyncClient, ASGITransport
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
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_agents_list(client):
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # The Council is exactly three deliberating agents plus the Moderator.
    assert {a["name"] for a in data} == {"oracle", "rag", "sage", "moderator"}


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
