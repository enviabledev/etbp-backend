import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_payment_unauthorized(client: AsyncClient):
    import uuid
    response = await client.get(f"/api/v1/payments/{uuid.uuid4()}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_paystack_webhook(client: AsyncClient):
    response = await client.post(
        "/api/v1/payments/webhook/paystack",
        json={"event": "charge.success", "data": {"reference": "nonexistent"}},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
