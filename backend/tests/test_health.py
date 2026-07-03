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
    assert len(data) >= 5


@pytest.mark.asyncio
async def test_agent_by_name(client):
    resp = await client.get("/api/agents/scout")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "scout"


@pytest.mark.asyncio
async def test_agent_not_found(client):
    resp = await client.get("/api/agents/nonexistent")
    assert resp.status_code == 404
