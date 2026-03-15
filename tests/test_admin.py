import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.core.security import hash_password
from app.models.booking import Booking, BookingPassenger
from app.models.notification import AuditLog, SupportTicket
from app.models.payment import Payment, PromoCode
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleType


async def _create_admin(db: AsyncSession, email: str = "admin@test.com") -> User:
    user = User(
        email=email, password_hash=hash_password("adminpass123"),
        first_name="Admin", last_name="User",
        role=UserRole.SUPER_ADMIN, email_verified=True, is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _login_admin(client: AsyncClient, email: str = "admin@test.com") -> str:
    resp = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "adminpass123",
    })
    return resp.json()["access_token"]


async def _setup_full(db: AsyncSession) -> dict:
    """Create admin, terminals, route, vehicle, trip with booking + payment."""
    admin = await _create_admin(db)

    passenger_user = User(
        email="passenger@test.com", password_hash=hash_password("pass123"),
        first_name="Jane", last_name="Doe", role=UserRole.PASSENGER,
    )
    db.add(passenger_user)
    await db.flush()

    lagos = Terminal(name="Lagos Terminal", code="LAG", city="Lagos", state="Lagos")
    ibadan = Terminal(name="Ibadan Terminal", code="IBD", city="Ibadan", state="Oyo")
    db.add_all([lagos, ibadan])
    await db.flush()

    vtype = VehicleType(name="Standard Bus", seat_capacity=18)
    db.add(vtype)
    await db.flush()

    vehicle = Vehicle(vehicle_type_id=vtype.id, plate_number="LAG-123-AB")
    db.add(vehicle)
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

    trip = Trip(
        schedule_id=schedule.id, route_id=route.id, vehicle_id=vehicle.id,
        departure_date=date.today() + timedelta(days=1),
        departure_time=time(8, 0), price=5500,
        available_seats=17, total_seats=18,
    )
    db.add(trip)
    await db.flush()

    seat = TripSeat(trip_id=trip.id, seat_number="A1", seat_row=1, seat_column=1, status="booked")
    db.add(seat)
    await db.flush()

    booking = Booking(
        reference="ET-TEST01", user_id=passenger_user.id, trip_id=trip.id,
        total_amount=5500, passenger_count=1,
        contact_email="passenger@test.com", status="confirmed",
    )
    db.add(booking)
    await db.flush()

    bp = BookingPassenger(
        booking_id=booking.id, seat_id=seat.id,
        first_name="Jane", last_name="Doe", is_primary=True,
        qr_code_data="ET-TEST01-A1-JANE",
    )
    db.add(bp)

    payment = Payment(
        booking_id=booking.id, user_id=passenger_user.id,
        amount=5500, method="card", status="successful",
        gateway="paystack", paid_at=datetime.now(timezone.utc),
    )
    db.add(payment)
    await db.flush()

    return {
        "admin": admin, "passenger": passenger_user,
        "lagos": lagos, "ibadan": ibadan,
        "vtype": vtype, "vehicle": vehicle,
        "route": route, "schedule": schedule, "trip": trip,
        "seat": seat, "booking": booking, "payment": payment,
    }


# ── Dashboard ──


@pytest.mark.asyncio
async def test_dashboard(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["users"]["total"] >= 2
    assert data["bookings"]["total"] >= 1
    assert data["revenue"]["total"] >= 5500
    assert data["trips"]["active"] >= 1


# ── Bookings by Status ──


@pytest.mark.asyncio
async def test_bookings_by_status(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/bookings-by-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(s["status"] == "confirmed" and s["count"] >= 1 for s in data)


# ── Revenue ──


@pytest.mark.asyncio
async def test_revenue_report(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/revenue",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["amount"] >= 5500


@pytest.mark.asyncio
async def test_revenue_by_route(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/revenue-by-route",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["route_code"] == "LAG-IBD"
    assert data[0]["revenue"] >= 5500


# ── Occupancy ──


@pytest.mark.asyncio
async def test_occupancy_report(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/occupancy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    trip = data[0]
    assert "occupancy_percent" in trip
    assert trip["total_seats"] == 18
    assert trip["booked_seats"] == 1


# ── Booking Trends ──


@pytest.mark.asyncio
async def test_booking_trends(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/booking-trends",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["confirmed"] >= 1


# ── Top Routes ──


@pytest.mark.asyncio
async def test_top_routes(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/top-routes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["booking_count"] >= 1


# ── Payment Methods ──


@pytest.mark.asyncio
async def test_payment_methods(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/payment-methods",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(m["method"] == "card" for m in data)


# ── User Growth ──


@pytest.mark.asyncio
async def test_user_growth(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/reports/user-growth",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ── Admin Bookings ──


@pytest.mark.asyncio
async def test_admin_list_bookings(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/bookings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_admin_list_bookings_filter_status(client: AsyncClient, db_session: AsyncSession):
    await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        "/api/admin/bookings?status=confirmed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_admin_check_in_booking(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.put(
        f"/api/admin/bookings/{f['booking'].id}/check-in",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "checked_in"
    assert data["passengers_checked_in"] == 1


# ── Admin Terminals & Routes ──


@pytest.mark.asyncio
async def test_admin_create_terminal(client: AsyncClient, db_session: AsyncSession):
    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        "/api/admin/routes/terminals",
        json={"name": "Abuja Terminal", "code": "ABJ", "city": "Abuja", "state": "FCT"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["code"] == "ABJ"


@pytest.mark.asyncio
async def test_admin_create_route(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        "/api/admin/routes",
        json={
            "name": "Lagos → Abuja", "code": "LAG-ABJ",
            "origin_terminal_id": str(f["lagos"].id),
            "destination_terminal_id": str(f["ibadan"].id),
            "base_price": 25000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201


# ── Admin Trips ──


@pytest.mark.asyncio
async def test_admin_create_trip_with_seats(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        "/api/admin/schedules/trips",
        json={
            "route_id": str(f["route"].id),
            "departure_date": str(date.today() + timedelta(days=5)),
            "departure_time": "09:00",
            "price": 6000,
            "total_seats": 8,
            "generate_seats": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    # Check seats were generated
    seats_resp = await client.get(f"/api/v1/trips/{trip_id}/seats")
    assert seats_resp.status_code == 200
    assert seats_resp.json()["total_seats"] == 8
    assert len(seats_resp.json()["seats"]) == 8


# ── Admin Vehicles ──


@pytest.mark.asyncio
async def test_admin_vehicle_crud(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        "/api/admin/vehicles",
        json={"vehicle_type_id": str(f["vtype"].id), "plate_number": "ABJ-999-XY", "make": "Toyota", "color": "White"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    vid = resp.json()["id"]

    resp = await client.put(
        f"/api/admin/vehicles/{vid}",
        json={"color": "Blue", "status": "maintenance"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["color"] == "Blue"
    assert resp.json()["status"] == "maintenance"


# ── Admin Users ──


@pytest.mark.asyncio
async def test_admin_user_detail_with_stats(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.get(
        f"/api/admin/users/{f['passenger'].id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "passenger@test.com"
    assert data["stats"]["total_bookings"] >= 1
    assert data["stats"]["total_spent"] >= 5500


@pytest.mark.asyncio
async def test_admin_deactivate_user(client: AsyncClient, db_session: AsyncSession):
    f = await _setup_full(db_session)
    token = await _login_admin(client)

    resp = await client.put(
        f"/api/admin/users/{f['passenger'].id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


# ── Promo CRUD ──


@pytest.mark.asyncio
async def test_admin_promo_crud(client: AsyncClient, db_session: AsyncSession):
    await _create_admin(db_session)
    token = await _login_admin(client)

    # Create
    resp = await client.post(
        "/api/admin/promos",
        json={"code": "SUMMER25", "discount_type": "percentage", "discount_value": 25, "max_discount": 5000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    pid = resp.json()["id"]

    # List
    resp = await client.get(
        "/api/admin/promos",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    # Deactivate
    resp = await client.put(
        f"/api/admin/promos/{pid}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


# ── RBAC: Non-admin denied ──


@pytest.mark.asyncio
async def test_non_admin_denied(client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="normie@test.com", password_hash=hash_password("pass123"),
        first_name="Normal", last_name="User", role=UserRole.PASSENGER,
    )
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={
        "email": "normie@test.com", "password": "pass123",
    })
    token = login.json()["access_token"]

    resp = await client.get(
        "/api/admin/reports/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
