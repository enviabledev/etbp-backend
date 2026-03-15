import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_bookings_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/bookings")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_bookings_empty(client: AsyncClient):
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "booking@example.com",
            "password": "securepass123",
            "first_name": "Test",
            "last_name": "User",
        },
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "booking@example.com", "password": "securepass123"},
    )
    token = login_resp.json()["access_token"]

    response = await client.get(
        "/api/v1/bookings", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json() == []
