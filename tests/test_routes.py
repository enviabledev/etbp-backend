import uuid
from datetime import date, time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.vehicle import VehicleType


async def _create_route_fixtures(db: AsyncSession) -> dict:
    """Create terminals, vehicle type, route, schedule, trip, and seats."""
    lagos = Terminal(name="Lagos Terminal", code="LAG", city="Lagos", state="Lagos")
    ibadan = Terminal(name="Ibadan Terminal", code="IBD", city="Ibadan", state="Oyo")
    db.add_all([lagos, ibadan])
    await db.flush()

    vtype = VehicleType(name="Standard Bus", seat_capacity=18)
    db.add(vtype)
    await db.flush()

    route = Route(
        name="Lagos → Ibadan",
        code="LAG-IBD",
        origin_terminal_id=lagos.id,
        destination_terminal_id=ibadan.id,
        distance_km=128,
        estimated_duration_minutes=150,
        base_price=5500,
    )
    db.add(route)
    await db.flush()

    schedule = Schedule(
        route_id=route.id,
        vehicle_type_id=vtype.id,
        departure_time=time(8, 0),
    )
    db.add(schedule)
    await db.flush()

    trip = Trip(
        schedule_id=schedule.id,
        route_id=route.id,
        departure_date=date(2026, 4, 1),
        departure_time=time(8, 0),
        price=5500,
        available_seats=3,
        total_seats=3,
    )
    db.add(trip)
    await db.flush()

    seats = []
    for i in range(1, 4):
        seat = TripSeat(
            trip_id=trip.id,
            seat_number=f"A{i}",
            seat_row=1,
            seat_column=i,
            seat_type="window" if i in (1, 3) else "aisle",
        )
        db.add(seat)
        seats.append(seat)
    await db.flush()

    return {
        "lagos": lagos,
        "ibadan": ibadan,
        "vtype": vtype,
        "route": route,
        "schedule": schedule,
        "trip": trip,
        "seats": seats,
    }


# ── Terminals ──


@pytest.mark.asyncio
async def test_list_terminals_empty(client: AsyncClient):
    response = await client.get("/api/v1/routes/terminals")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_terminals(client: AsyncClient, db_session: AsyncSession):
    terminal = Terminal(name="Lagos Terminal", code="LAG", city="Lagos", state="Lagos")
    db_session.add(terminal)
    await db_session.flush()

    response = await client.get("/api/v1/routes/terminals")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["code"] == "LAG"


@pytest.mark.asyncio
async def test_terminals_autocomplete(client: AsyncClient, db_session: AsyncSession):
    lagos = Terminal(name="Lagos Jibowu", code="LAG", city="Lagos", state="Lagos")
    abuja = Terminal(name="Abuja Utako", code="ABJ", city="Abuja", state="FCT")
    db_session.add_all([lagos, abuja])
    await db_session.flush()

    response = await client.get("/api/v1/terminals?search=lag")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["code"] == "LAG"


@pytest.mark.asyncio
async def test_terminals_autocomplete_no_match(client: AsyncClient, db_session: AsyncSession):
    db_session.add(Terminal(name="Lagos", code="LAG", city="Lagos", state="Lagos"))
    await db_session.flush()

    response = await client.get("/api/v1/terminals?search=kano")
    assert response.status_code == 200
    assert response.json() == []


# ── Routes ──


@pytest.mark.asyncio
async def test_list_routes_empty(client: AsyncClient):
    response = await client.get("/api/v1/routes")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_route_not_found(client: AsyncClient):
    response = await client.get(f"/api/v1/routes/{uuid.uuid4()}")
    assert response.status_code == 404


# ── Route Search ──


@pytest.mark.asyncio
async def test_search_trips_by_origin_destination(
    client: AsyncClient, db_session: AsyncSession
):
    fixtures = await _create_route_fixtures(db_session)

    response = await client.get(
        "/api/v1/routes/search?origin=Lagos&destination=Ibadan&date=2026-04-01"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    trip = data[0]
    assert trip["route"]["code"] == "LAG-IBD"
    assert trip["price"] == 5500
    assert trip["available_seats"] == 3
    assert trip["vehicle_type"]["name"] == "Standard Bus"
    assert trip["estimated_duration_minutes"] == 150


@pytest.mark.asyncio
async def test_search_trips_by_terminal_code(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_route_fixtures(db_session)

    response = await client.get("/api/v1/routes/search?origin=LAG&date=2026-04-01")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_search_trips_filters_by_passengers(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_route_fixtures(db_session)

    # 3 available seats, asking for 4 → no results
    response = await client.get(
        "/api/v1/routes/search?origin=Lagos&date=2026-04-01&passengers=4"
    )
    assert response.status_code == 200
    assert response.json() == []

    # Asking for 3 → should match
    response = await client.get(
        "/api/v1/routes/search?origin=Lagos&date=2026-04-01&passengers=3"
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_search_trips_no_results(client: AsyncClient, db_session: AsyncSession):
    await _create_route_fixtures(db_session)

    response = await client.get(
        "/api/v1/routes/search?origin=Kano&destination=Enugu&date=2026-04-01"
    )
    assert response.status_code == 200
    assert response.json() == []


# ── Trips ──


@pytest.mark.asyncio
async def test_list_trips_by_route(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)

    response = await client.get(
        f"/api/v1/trips?route_id={fixtures['route'].id}&date=2026-04-01"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(fixtures["trip"].id)


@pytest.mark.asyncio
async def test_get_trip_detail(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)

    response = await client.get(f"/api/v1/trips/{fixtures['trip'].id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_seats"] == 3
    assert len(data["seats"]) == 3


# ── Seat Map ──


@pytest.mark.asyncio
async def test_get_seat_map(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)

    response = await client.get(f"/api/v1/trips/{fixtures['trip'].id}/seats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_seats"] == 3
    assert data["available_seats"] == 3
    assert len(data["seats"]) == 3
    assert all(s["status"] == "available" for s in data["seats"])


@pytest.mark.asyncio
async def test_get_seat_map_not_found(client: AsyncClient):
    response = await client.get(f"/api/v1/trips/{uuid.uuid4()}/seats")
    assert response.status_code == 404


# ── Seat Locking ──


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "securepass123",
            "first_name": "Test",
            "last_name": "User",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "securepass123"},
    )
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_lock_seats(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)
    token = await _register_and_login(client, "locker@example.com")
    seat_ids = [str(s.id) for s in fixtures["seats"][:2]]

    response = await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["locked_seats"]) == 2
    assert "locked_until" in data
    assert "5 minutes" in data["message"]


@pytest.mark.asyncio
async def test_lock_seats_conflict(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)
    token1 = await _register_and_login(client, "user1@example.com")
    token2 = await _register_and_login(client, "user2@example.com")

    seat_ids = [str(fixtures["seats"][0].id)]

    # User 1 locks seat
    resp1 = await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp1.status_code == 200

    # User 2 tries to lock same seat → 409
    resp2 = await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp2.status_code == 409
    assert "already taken" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_lock_seats_same_user_extends(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)
    token = await _register_and_login(client, "extender@example.com")
    seat_ids = [str(fixtures["seats"][0].id)]

    # Lock once
    await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Lock again by same user → should succeed (extend)
    resp = await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lock_seats_unauthorized(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)
    seat_ids = [str(fixtures["seats"][0].id)]

    response = await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_seat_map_reflects_locks(client: AsyncClient, db_session: AsyncSession):
    fixtures = await _create_route_fixtures(db_session)
    token = await _register_and_login(client, "viewer@example.com")
    seat_ids = [str(fixtures["seats"][0].id)]

    # Lock one seat
    await client.post(
        f"/api/v1/trips/{fixtures['trip'].id}/seats/lock",
        json={"seat_ids": seat_ids},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Seat map should show 1 locked, 2 available
    response = await client.get(f"/api/v1/trips/{fixtures['trip'].id}/seats")
    assert response.status_code == 200
    data = response.json()
    statuses = {s["status"] for s in data["seats"]}
    assert "locked" in statuses
    locked_count = sum(1 for s in data["seats"] if s["status"] == "locked")
    available_count = sum(1 for s in data["seats"] if s["status"] == "available")
    assert locked_count == 1
    assert available_count == 2


# ── Popular Routes ──


@pytest.mark.asyncio
async def test_popular_routes_empty(client: AsyncClient):
    response = await client.get("/api/v1/routes/popular")
    assert response.status_code == 200
    assert response.json() == []
