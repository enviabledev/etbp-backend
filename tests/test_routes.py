import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route, Terminal


@pytest.mark.asyncio
async def test_list_terminals_empty(client: AsyncClient):
    response = await client.get("/api/v1/routes/terminals")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_routes_empty(client: AsyncClient):
    response = await client.get("/api/v1/routes")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_terminals(client: AsyncClient, db_session: AsyncSession):
    terminal = Terminal(
        name="Lagos Terminal",
        code="LAG",
        city="Lagos",
        state="Lagos",
    )
    db_session.add(terminal)
    await db_session.flush()

    response = await client.get("/api/v1/routes/terminals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["code"] == "LAG"


@pytest.mark.asyncio
async def test_get_route_not_found(client: AsyncClient):
    import uuid
    response = await client.get(f"/api/v1/routes/{uuid.uuid4()}")
    assert response.status_code == 404
