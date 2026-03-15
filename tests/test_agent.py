import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BookingStatus, UserRole
from app.core.security import hash_password
from app.models.booking import Booking, BookingPassenger
from app.models.payment import Payment
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.user import User
from app.models.vehicle import VehicleType


async def _setup(db: AsyncSession) -> dict:
    agent = User(
        email="agent@test.com", password_hash=hash_password("agentpass123"),
        first_name="Agent", last_name="Smith", role=UserRole.AGENT,
    )
    db.add(agent)
    await db.flush()

    lagos = Terminal(name="Lagos Terminal", code="LAG", city="Lagos", state="Lagos")
    ibadan = Terminal(name="Ibadan Terminal", code="IBD", city="Ibadan", state="Oyo")
    db.add_all([lagos, ibadan])
    await db.flush()

    vtype = VehicleType(name="Bus", seat_capacity=18)
    db.add(vtype)
    await db.flush()

    route = Route(
        name="Lagos → Ibadan", code="LAG-IBD",
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
        departure_date=date.today() + timedelta(days=1),
        departure_time=time(8, 0), price=5500,
        available_seats=4, total_seats=4,
    )
    db.add(trip)
    await db.flush()

    seats = []
    for i in range(1, 5):
        seat = TripSeat(trip_id=trip.id, seat_number=f"A{i}", seat_row=1, seat_column=i)
        db.add(seat)
        seats.append(seat)
    await db.flush()

    return {
        "agent": agent, "route": route, "trip": trip,
        "seats": seats, "lagos": lagos, "schedule": schedule,
    }


async def _login_agent(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/auth/login", json={
        "email": "agent@test.com", "password": "agentpass123",
    })
    return resp.json()["access_token"]


# ── Agent Booking (walk-in, cash, skip lock) ──


@pytest.mark.asyncio
async def test_agent_create_booking_cash(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Walk", "last_name": "In", "is_primary": True},
            ],
            "contact_phone": "+2348011111111",
            "payment_method": "cash",
            "collect_payment": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["reference"].startswith("ET-")
    assert data["status"] == "confirmed"  # Cash = auto-confirmed
    assert data["total_amount"] == 5500.0
    assert len(data["passengers"]) == 1


@pytest.mark.asyncio
async def test_agent_create_booking_multiple_passengers(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "John", "last_name": "Doe", "is_primary": True},
                {"seat_id": str(f["seats"][1].id), "first_name": "Jane", "last_name": "Doe"},
            ],
            "contact_phone": "+2348022222222",
            "payment_method": "cash",
            "collect_payment": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["passenger_count"] == 2
    assert resp.json()["total_amount"] == 11000.0


@pytest.mark.asyncio
async def test_agent_create_booking_no_payment(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "P", "last_name": "U", "is_primary": True},
            ],
            "contact_phone": "+2348033333333",
            "collect_payment": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"  # No payment yet


@pytest.mark.asyncio
async def test_agent_booking_reuses_existing_user(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    # Create a user with this phone
    existing = User(
        phone="+2348044444444", first_name="Existing", last_name="User",
        password_hash=hash_password("pass"), role=UserRole.PASSENGER,
    )
    db_session.add(existing)
    await db_session.flush()

    resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Existing", "last_name": "User", "is_primary": True},
            ],
            "contact_phone": "+2348044444444",
            "collect_payment": True,
            "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["user_id"] == str(existing.id)


@pytest.mark.asyncio
async def test_non_agent_denied(client: AsyncClient, db_session: AsyncSession):
    passenger = User(
        email="notanagent@test.com", password_hash=hash_password("pass123"),
        first_name="Not", last_name="Agent", role=UserRole.PASSENGER,
    )
    db_session.add(passenger)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={
        "email": "notanagent@test.com", "password": "pass123",
    })
    token = login.json()["access_token"]

    resp = await client.post(
        "/api/agent/bookings",
        json={"trip_id": str(uuid.uuid4()), "passengers": [], "collect_payment": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Search ──


@pytest.mark.asyncio
async def test_agent_search_by_reference(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    # Create a booking
    book_resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Search", "last_name": "Me", "is_primary": True},
            ],
            "contact_phone": "+2348055555555",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    ref = book_resp.json()["reference"]

    resp = await client.get(
        f"/api/agent/bookings/search?reference={ref}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["reference"] == ref


@pytest.mark.asyncio
async def test_agent_search_by_passenger_name(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Unique", "last_name": "Passenger", "is_primary": True},
            ],
            "contact_phone": "+2348066666666",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/bookings/search?passenger_name=Unique",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ── Check-in ──


@pytest.mark.asyncio
async def test_check_in_by_reference(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    book_resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Check", "last_name": "In", "is_primary": True},
                {"seat_id": str(f["seats"][1].id), "first_name": "Also", "last_name": "Here"},
            ],
            "contact_phone": "+2348077777777",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    ref = book_resp.json()["reference"]

    resp = await client.post(
        "/api/agent/bookings/check-in",
        json={"booking_reference": ref},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["passengers_checked_in"] == 2
    assert data["status"] == "checked_in"


@pytest.mark.asyncio
async def test_check_in_by_qr_code(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    book_resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "QR", "last_name": "Scan", "is_primary": True},
            ],
            "contact_phone": "+2348088888888",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    qr_code = book_resp.json()["passengers"][0]["qr_code_data"]

    resp = await client.post(
        "/api/agent/bookings/check-in",
        json={"qr_code": qr_code},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["checked_in"] is True


@pytest.mark.asyncio
async def test_check_in_already_checked_in(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    book_resp = await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Double", "last_name": "Check", "is_primary": True},
            ],
            "contact_phone": "+2348099999999",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    qr_code = book_resp.json()["passengers"][0]["qr_code_data"]

    # First check-in
    await client.post(
        "/api/agent/bookings/check-in", json={"qr_code": qr_code},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Second check-in
    resp = await client.post(
        "/api/agent/bookings/check-in", json={"qr_code": qr_code},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "already checked in" in resp.json()["detail"]


# ── Manifest ──


@pytest.mark.asyncio
async def test_trip_manifest(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    # Book 2 passengers
    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Mani", "last_name": "Fest", "is_primary": True},
                {"seat_id": str(f["seats"][1].id), "first_name": "Also", "last_name": "Here"},
            ],
            "contact_phone": "+2348011112222",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/api/agent/bookings/manifest/{f['trip'].id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["booked_passengers"] == 2
    assert data["checked_in_count"] == 0
    assert len(data["passengers"]) == 2
    assert data["passengers"][0]["seat_number"] in ("A1", "A2")


# ── Agent Reports ──


@pytest.mark.asyncio
async def test_agent_dashboard(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    # Create a booking today
    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Dash", "last_name": "Board", "is_primary": True},
            ],
            "contact_phone": "+2348022223333",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/reports/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"]["bookings"] >= 1
    assert data["today"]["revenue"] >= 5500
    assert data["today"]["confirmed"] >= 1


@pytest.mark.asyncio
async def test_agent_summary(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Sum", "last_name": "Mary", "is_primary": True},
            ],
            "contact_phone": "+2348033334444",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/reports/my-bookings-summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_bookings"] >= 1
    assert data["total_amount"] >= 5500
    assert data["confirmed"] >= 1


@pytest.mark.asyncio
async def test_agent_daily_breakdown(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Day", "last_name": "Break", "is_primary": True},
            ],
            "contact_phone": "+2348044445555",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/reports/daily-breakdown",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["bookings"] >= 1


@pytest.mark.asyncio
async def test_agent_top_routes(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Top", "last_name": "Route", "is_primary": True},
            ],
            "contact_phone": "+2348055556666",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/reports/top-routes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["route_code"] == "LAG-IBD"


@pytest.mark.asyncio
async def test_agent_payment_methods(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login_agent(client)

    await client.post(
        "/api/agent/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [
                {"seat_id": str(f["seats"][0].id), "first_name": "Pay", "last_name": "Method", "is_primary": True},
            ],
            "contact_phone": "+2348066667777",
            "collect_payment": True, "payment_method": "cash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/agent/reports/payment-methods",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(m["method"] == "cash" for m in data)
