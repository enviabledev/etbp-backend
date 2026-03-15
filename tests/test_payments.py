import hashlib
import hmac
import json
import uuid
from datetime import date, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.payment import Payment, Wallet, WalletTransaction
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.vehicle import VehicleType


# ── Fixtures ──


async def _setup_and_book(client: AsyncClient, db: AsyncSession) -> tuple[str, str, dict]:
    """Create trip, register user, lock seats, book. Return (token, reference, fixtures)."""
    lagos = Terminal(name="Lagos", code="LAG", city="Lagos", state="Lagos")
    ibadan = Terminal(name="Ibadan", code="IBD", city="Ibadan", state="Oyo")
    db.add_all([lagos, ibadan])
    await db.flush()

    vtype = VehicleType(name="Bus", seat_capacity=18)
    db.add(vtype)
    await db.flush()

    route = Route(
        name="LAG-IBD", code="LAG-IBD",
        origin_terminal_id=lagos.id, destination_terminal_id=ibadan.id,
        distance_km=128, estimated_duration_minutes=150, base_price=5500,
    )
    db.add(route)
    await db.flush()

    schedule = Schedule(route_id=route.id, vehicle_type_id=vtype.id, departure_time=time(8, 0))
    db.add(schedule)
    await db.flush()

    trip = Trip(
        schedule_id=schedule.id, route_id=route.id,
        departure_date=date.today() + timedelta(days=2),
        departure_time=time(8, 0), price=5500,
        available_seats=4, total_seats=4,
    )
    db.add(trip)
    await db.flush()

    seat = TripSeat(trip_id=trip.id, seat_number="A1", seat_row=1, seat_column=1)
    db.add(seat)
    await db.flush()

    # Register + login
    await client.post("/api/v1/auth/register", json={
        "email": f"pay{uuid.uuid4().hex[:6]}@test.com", "password": "securepass123",
        "first_name": "Pay", "last_name": "User",
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": f"pay{uuid.uuid4().hex[:6]}@test.com", "password": "securepass123",
    })
    # Re-register with fixed email for this test
    email = f"p{uuid.uuid4().hex[:8]}@test.com"
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": "securepass123",
        "first_name": "Pay", "last_name": "User",
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "securepass123",
    })
    token = login.json()["access_token"]

    # Lock + book
    await client.post(
        f"/api/v1/trips/{trip.id}/seats/lock",
        json={"seat_ids": [str(seat.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    book_resp = await client.post(
        "/api/v1/bookings",
        json={
            "trip_id": str(trip.id),
            "passengers": [{"seat_id": str(seat.id), "first_name": "P", "last_name": "U", "is_primary": True}],
            "contact_email": email,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    ref = book_resp.json()["reference"]

    return token, ref, {"trip": trip, "seat": seat}


# ── Payment Initiation ──


@pytest.mark.asyncio
async def test_initiate_payment(client: AsyncClient, db_session: AsyncSession):
    token, ref, fixtures = await _setup_and_book(client, db_session)

    # Get booking_id
    resp = await client.get(
        f"/api/v1/bookings/{ref}",
        headers={"Authorization": f"Bearer {token}"},
    )
    booking_id = resp.json()["id"]

    mock_paystack = AsyncMock()
    mock_paystack.initialize_transaction.return_value = {
        "authorization_url": "https://checkout.paystack.com/test123",
        "reference": "PSK-REF-123",
    }

    with patch("app.services.payment_service.PaystackClient", return_value=mock_paystack):
        resp = await client.post(
            "/api/v1/payments/initiate",
            json={"booking_id": booking_id, "method": "card"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["authorization_url"] == "https://checkout.paystack.com/test123"
    assert data["reference"] == "PSK-REF-123"


@pytest.mark.asyncio
async def test_get_payment_unauthorized(client: AsyncClient):
    response = await client.get(f"/api/v1/payments/{uuid.uuid4()}")
    assert response.status_code == 401


# ── Paystack Webhook ──


@pytest.mark.asyncio
async def test_paystack_webhook_confirms_booking(client: AsyncClient, db_session: AsyncSession):
    token, ref, _ = await _setup_and_book(client, db_session)

    # Get booking id
    resp = await client.get(
        f"/api/v1/bookings/{ref}",
        headers={"Authorization": f"Bearer {token}"},
    )
    booking_id = resp.json()["id"]

    # Create a payment record directly
    from app.models.user import User
    user_result = await db_session.execute(
        select(User).where(User.email.ilike("%@test.com")).limit(1)
    )

    booking_result = await db_session.execute(
        select(Booking).where(Booking.reference == ref)
    )
    booking = booking_result.scalar_one()

    payment = Payment(
        booking_id=booking.id, user_id=booking.user_id,
        amount=5500, method="card", gateway="paystack",
        gateway_reference="webhook-ref-123",
    )
    db_session.add(payment)
    await db_session.flush()

    # Send webhook
    payload = {
        "event": "charge.success",
        "data": {"reference": "webhook-ref-123", "amount": 550000},
    }
    resp = await client.post(
        "/api/v1/payments/webhook/paystack",
        json=payload,
    )
    assert resp.status_code == 200

    # Verify booking is confirmed
    booking_resp = await client.get(
        f"/api/v1/bookings/{ref}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert booking_resp.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_paystack_webhook_idempotent(client: AsyncClient, db_session: AsyncSession):
    token, ref, _ = await _setup_and_book(client, db_session)

    booking_result = await db_session.execute(
        select(Booking).where(Booking.reference == ref)
    )
    booking = booking_result.scalar_one()

    payment = Payment(
        booking_id=booking.id, user_id=booking.user_id,
        amount=5500, method="card", gateway="paystack",
        gateway_reference="idempotent-ref", status="successful",
    )
    db_session.add(payment)
    await db_session.flush()

    # Send webhook twice
    payload = {"event": "charge.success", "data": {"reference": "idempotent-ref"}}
    resp1 = await client.post("/api/v1/payments/webhook/paystack", json=payload)
    resp2 = await client.post("/api/v1/payments/webhook/paystack", json=payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_paystack_webhook_unknown_reference(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/v1/payments/webhook/paystack",
        json={"event": "charge.success", "data": {"reference": "unknown-xyz"}},
    )
    assert resp.status_code == 200  # Graceful no-op


# ── Wallet ──


@pytest.mark.asyncio
async def test_get_wallet_creates_if_missing(client: AsyncClient, db_session: AsyncSession):
    await client.post("/api/v1/auth/register", json={
        "email": "wallet@test.com", "password": "securepass123",
        "first_name": "W", "last_name": "U",
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": "wallet@test.com", "password": "securepass123",
    })
    token = login.json()["access_token"]

    resp = await client.get(
        "/api/v1/payments/wallet/balance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == 0.0
    assert data["currency"] == "NGN"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_pay_with_wallet(client: AsyncClient, db_session: AsyncSession):
    token, ref, _ = await _setup_and_book(client, db_session)

    # Get user_id and fund wallet
    booking_result = await db_session.execute(
        select(Booking).where(Booking.reference == ref)
    )
    booking = booking_result.scalar_one()

    wallet = Wallet(user_id=booking.user_id, balance=10000)
    db_session.add(wallet)
    await db_session.flush()

    resp = await client.post(
        "/api/v1/payments/pay-with-wallet",
        json={"booking_id": str(booking.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_reference"] == ref
    assert data["amount_paid"] == 5500.0
    assert data["wallet_balance"] == 4500.0
    assert data["booking_status"] == "confirmed"


@pytest.mark.asyncio
async def test_pay_with_wallet_insufficient_balance(client: AsyncClient, db_session: AsyncSession):
    token, ref, _ = await _setup_and_book(client, db_session)

    booking_result = await db_session.execute(
        select(Booking).where(Booking.reference == ref)
    )
    booking = booking_result.scalar_one()

    wallet = Wallet(user_id=booking.user_id, balance=100)  # Not enough
    db_session.add(wallet)
    await db_session.flush()

    resp = await client.post(
        "/api/v1/payments/pay-with-wallet",
        json={"booking_id": str(booking.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "Insufficient" in resp.json()["detail"]
