import uuid
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.payment import Payment, PromoCode, Wallet, WalletTransaction
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.vehicle import VehicleType


# ── Fixtures ──


async def _setup_trip(db: AsyncSession) -> dict:
    lagos = Terminal(name="Lagos Terminal", code="LAG", city="Lagos", state="Lagos")
    ibadan = Terminal(name="Ibadan Terminal", code="IBD", city="Ibadan", state="Oyo")
    db.add_all([lagos, ibadan])
    await db.flush()

    vtype = VehicleType(name="Standard Bus", seat_capacity=18)
    db.add(vtype)
    await db.flush()

    route = Route(
        name="Lagos → Ibadan", code="LAG-IBD",
        origin_terminal_id=lagos.id, destination_terminal_id=ibadan.id,
        distance_km=128, estimated_duration_minutes=150, base_price=5500,
    )
    db.add(route)
    await db.flush()

    schedule = Schedule(
        route_id=route.id, vehicle_type_id=vtype.id, departure_time=time(8, 0),
    )
    db.add(schedule)
    await db.flush()

    # Trip 2 days from now so cancellation refund rules can be tested
    trip = Trip(
        schedule_id=schedule.id, route_id=route.id,
        departure_date=date.today() + timedelta(days=2),
        departure_time=time(8, 0), price=5500,
        available_seats=4, total_seats=4,
    )
    db.add(trip)
    await db.flush()

    seats = []
    for i in range(1, 5):
        seat = TripSeat(
            trip_id=trip.id, seat_number=f"A{i}",
            seat_row=1, seat_column=i, seat_type="window" if i % 2 else "aisle",
        )
        db.add(seat)
        seats.append(seat)
    await db.flush()

    return {"route": route, "trip": trip, "seats": seats, "schedule": schedule}


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": "securepass123",
        "first_name": "Test", "last_name": "User",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "securepass123",
    })
    return resp.json()["access_token"]


async def _lock_and_book(
    client: AsyncClient, db: AsyncSession, token: str, trip_id, seat_ids, email="book@test.com"
) -> dict:
    """Lock seats then create booking, return booking response."""
    # Lock
    await client.post(
        f"/api/v1/trips/{trip_id}/seats/lock",
        json={"seat_ids": [str(s) for s in seat_ids]},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Book
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "trip_id": str(trip_id),
            "passengers": [
                {
                    "seat_id": str(sid),
                    "first_name": f"Passenger{i}",
                    "last_name": "Test",
                    "is_primary": i == 0,
                }
                for i, sid in enumerate(seat_ids)
            ],
            "contact_email": email,
            "contact_phone": "+2348012345678",
            "emergency_contact_name": "Emergency Person",
            "emergency_contact_phone": "+2348099999999",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


# ── Booking CRUD ──


@pytest.mark.asyncio
async def test_create_booking(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "booker@example.com")

    resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["reference"].startswith("ET-")
    assert len(data["reference"]) == 9  # ET-XXXXXX
    assert data["status"] == "pending"
    assert data["passenger_count"] == 1
    assert data["total_amount"] == 5500.0
    assert len(data["passengers"]) == 1
    assert data["passengers"][0]["qr_code_data"] is not None
    assert data["emergency_contact_name"] == "Emergency Person"


@pytest.mark.asyncio
async def test_create_booking_seats_must_be_locked(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "nolock@example.com")

    # Try booking without locking
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "trip_id": str(fixtures["trip"].id),
            "passengers": [{"seat_id": str(fixtures["seats"][0].id), "first_name": "A", "last_name": "B"}],
            "contact_email": "a@b.com",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "not locked" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_bookings_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/bookings")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_bookings_empty(client: AsyncClient, db_session: AsyncSession):
    token = await _register_and_login(client, "empty@example.com")
    response = await client.get(
        "/api/v1/bookings", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_booking_by_reference(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "ref@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.get(
        f"/api/v1/bookings/{ref}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["reference"] == ref


# ── Cancellation with Refund Rules ──


@pytest.mark.asyncio
async def test_cancel_booking_refund_90_percent(client: AsyncClient, db_session: AsyncSession):
    """Trip >24h away → 90% refund."""
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "cancel90@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.put(
        f"/api/v1/bookings/{ref}/cancel",
        json={"reason": "Change of plans"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["refund_percentage"] == 90
    assert data["refund_amount"] == 4950.0  # 90% of 5500


@pytest.mark.asyncio
async def test_cancel_booking_already_cancelled(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "cancel2x@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    await client.put(
        f"/api/v1/bookings/{ref}/cancel", json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.put(
        f"/api/v1/bookings/{ref}/cancel", json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cancel_releases_seats(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "release@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id, fixtures["seats"][1].id],
    )
    ref = book_resp.json()["reference"]

    # Before cancel: 2 seats used
    seat_resp = await client.get(f"/api/v1/trips/{fixtures['trip'].id}/seats")
    booked_count = sum(1 for s in seat_resp.json()["seats"] if s["status"] == "booked")
    assert booked_count == 2

    await client.put(
        f"/api/v1/bookings/{ref}/cancel", json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    # After cancel: seats released
    seat_resp = await client.get(f"/api/v1/trips/{fixtures['trip'].id}/seats")
    available_count = sum(1 for s in seat_resp.json()["seats"] if s["status"] == "available")
    assert available_count == 4


# ── Reschedule ──


@pytest.mark.asyncio
async def test_reschedule_booking(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "resched@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    # Confirm the booking (simulate payment)
    booking_result = await db_session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(Booking).where(
            Booking.reference == ref
        )
    )
    booking = booking_result.scalar_one()
    booking.status = "confirmed"
    await db_session.flush()

    # Create a second trip
    trip2 = Trip(
        schedule_id=fixtures["schedule"].id, route_id=fixtures["route"].id,
        departure_date=date.today() + timedelta(days=3),
        departure_time=time(10, 0), price=6000,
        available_seats=2, total_seats=2,
    )
    db_session.add(trip2)
    await db_session.flush()

    seat_new = TripSeat(
        trip_id=trip2.id, seat_number="B1", seat_row=1, seat_column=1,
    )
    db_session.add(seat_new)
    await db_session.flush()

    resp = await client.put(
        f"/api/v1/bookings/{ref}/reschedule",
        json={"new_trip_id": str(trip2.id), "new_seat_ids": [str(seat_new.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["trip_id"] == str(trip2.id)
    assert data["total_amount"] == 6000.0


# ── E-Ticket ──


@pytest.mark.asyncio
async def test_download_eticket(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "ticket@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.get(
        f"/api/v1/bookings/{ref}/ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 100  # Non-trivial PDF


# ── Promo Codes ──


@pytest.mark.asyncio
async def test_apply_promo_percentage(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "promo@example.com")

    promo = PromoCode(
        code="SAVE20", discount_type="percentage", discount_value=20,
        max_discount=2000, is_active=True,
    )
    db_session.add(promo)
    await db_session.flush()

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "SAVE20"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["original_amount"] == 5500.0
    assert data["discount_amount"] == 1100.0  # 20% of 5500
    assert data["new_total"] == 4400.0
    assert data["promo_code"] == "SAVE20"


@pytest.mark.asyncio
async def test_apply_promo_fixed(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "promof@example.com")

    promo = PromoCode(
        code="FLAT500", discount_type="fixed", discount_value=500, is_active=True,
    )
    db_session.add(promo)
    await db_session.flush()

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "FLAT500"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["discount_amount"] == 500.0
    assert resp.json()["new_total"] == 5000.0


@pytest.mark.asyncio
async def test_apply_promo_max_discount_cap(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "promocap@example.com")

    promo = PromoCode(
        code="BIG50", discount_type="percentage", discount_value=50,
        max_discount=1000, is_active=True,
    )
    db_session.add(promo)
    await db_session.flush()

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "BIG50"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    # 50% of 5500 = 2750, but capped at 1000
    assert resp.json()["discount_amount"] == 1000.0
    assert resp.json()["new_total"] == 4500.0


@pytest.mark.asyncio
async def test_apply_promo_invalid_code(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "badpromo@example.com")

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "DOESNOTEXIST"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_promo_expired(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "expired@example.com")

    promo = PromoCode(
        code="OLD", discount_type="fixed", discount_value=100, is_active=True,
        valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(promo)
    await db_session.flush()

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "OLD"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_apply_promo_min_amount(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _setup_trip(db_session)
    token = await _register_and_login(client, "minamt@example.com")

    promo = PromoCode(
        code="HIGHMIN", discount_type="fixed", discount_value=100,
        min_booking_amount=10000, is_active=True,
    )
    db_session.add(promo)
    await db_session.flush()

    book_resp = await _lock_and_book(
        client, db_session, token,
        fixtures["trip"].id, [fixtures["seats"][0].id],
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        f"/api/v1/bookings/{ref}/apply-promo",
        json={"promo_code": "HIGHMIN"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "Minimum" in resp.json()["detail"]
