import uuid
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BookingStatus, NotificationChannel
from app.core.security import hash_password
from app.models.booking import Booking, BookingPassenger
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.user import User
from app.models.vehicle import VehicleType
from app.services import notification_service


# ── Fixtures ──


async def _setup(db: AsyncSession) -> dict:
    user = User(
        email="notify@test.com", password_hash=hash_password("pass123"),
        first_name="Notify", last_name="User", role="passenger",
    )
    db.add(user)
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
        departure_date=date.today() + timedelta(days=2),
        departure_time=time(8, 0), price=5500,
        available_seats=4, total_seats=4,
    )
    db.add(trip)
    await db.flush()

    seat = TripSeat(trip_id=trip.id, seat_number="A1", seat_row=1, seat_column=1)
    db.add(seat)
    await db.flush()

    return {"user": user, "route": route, "trip": trip, "seat": seat, "lagos": lagos}


async def _login(client: AsyncClient, email: str = "notify@test.com") -> str:
    resp = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "pass123",
    })
    return resp.json()["access_token"]


# ── Template Rendering ──


def test_render_booking_confirmed_template():
    html = notification_service.render_template(
        "booking_confirmed.html",
        passenger_name="John Doe",
        booking_reference="ET-ABC123",
        route_name="Lagos → Ibadan",
        departure_date="15 Apr 2026",
        departure_time="08:00",
        seat_numbers="A1, A2",
        passenger_count=2,
        currency="NGN",
        amount="11,000.00",
    )
    assert "ET-ABC123" in html
    assert "John Doe" in html
    assert "Lagos → Ibadan" in html
    assert "A1, A2" in html
    assert "NGN" in html


def test_render_booking_cancelled_template():
    html = notification_service.render_template(
        "booking_cancelled.html",
        passenger_name="Jane Doe",
        booking_reference="ET-XYZ789",
        route_name="Lagos → Abuja",
        departure_date="20 Apr 2026",
        reason="Change of plans",
        currency="NGN",
        refund_amount=4950,
        refund_percentage=90,
    )
    assert "ET-XYZ789" in html
    assert "4950" in html
    assert "90%" in html


def test_render_payment_receipt_template():
    html = notification_service.render_template(
        "payment_receipt.html",
        passenger_name="John Doe",
        booking_reference="ET-ABC123",
        currency="NGN",
        amount="5,500.00",
        payment_method="card",
        payment_reference="PSK-123",
        payment_date="15 Mar 2026 10:30",
    )
    assert "Payment Receipt" in html
    assert "PSK-123" in html


def test_render_trip_reminder_template():
    html = notification_service.render_template(
        "trip_reminder.html",
        passenger_name="John Doe",
        booking_reference="ET-ABC123",
        route_name="Lagos → Ibadan",
        departure_date="16 Apr 2026",
        departure_time="08:00",
        terminal_name="Lagos Terminal (Jibowu)",
        seat_numbers="A1",
    )
    assert "Trip Reminder" in html
    assert "tomorrow" in html
    assert "30 minutes" in html


# ── In-app Notification CRUD ──


@pytest.mark.asyncio
async def test_create_in_app_notification(db_session: AsyncSession):
    f = await _setup(db_session)

    notif = await notification_service.create_notification(
        db_session, f["user"].id,
        channel=NotificationChannel.IN_APP,
        title="Test", body="Test body",
        data={"key": "value"},
    )
    assert notif.id is not None
    assert notif.is_read is False
    assert notif.channel == "in_app"


@pytest.mark.asyncio
async def test_list_notifications(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login(client)

    # Create a few notifications
    for i in range(3):
        await notification_service.create_notification(
            db_session, f["user"].id,
            channel=NotificationChannel.IN_APP,
            title=f"Notif {i}", body=f"Body {i}",
        )

    resp = await client.get(
        "/api/v1/users/me/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["unread_count"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_mark_notification_read(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login(client)

    notif = await notification_service.create_notification(
        db_session, f["user"].id,
        channel=NotificationChannel.IN_APP, title="Read me", body="body",
    )

    resp = await client.put(
        f"/api/v1/users/me/notifications/{notif.id}/read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    # Verify
    resp = await client.get(
        "/api/v1/users/me/notifications?is_read=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_mark_all_read(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login(client)

    for i in range(5):
        await notification_service.create_notification(
            db_session, f["user"].id,
            channel=NotificationChannel.IN_APP, title=f"N{i}", body="b",
        )

    resp = await client.put(
        "/api/v1/users/me/notifications/read-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "5" in resp.json()["message"]

    # All read now
    resp = await client.get(
        "/api/v1/users/me/notifications/unread-count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_unread_count(client: AsyncClient, db_session: AsyncSession):
    f = await _setup(db_session)
    token = await _login(client)

    for i in range(3):
        await notification_service.create_notification(
            db_session, f["user"].id,
            channel=NotificationChannel.IN_APP, title=f"N{i}", body="b",
        )

    resp = await client.get(
        "/api/v1/users/me/notifications/unread-count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["unread_count"] == 3


# ── Notification Dispatch (email/SMS mocked) ──


@pytest.mark.asyncio
async def test_notify_booking_confirmed_creates_in_app(db_session: AsyncSession):
    f = await _setup(db_session)

    with patch("app.tasks.email_tasks.send_templated_email") as mock_email, \
         patch("app.tasks.sms_tasks.send_sms_task") as mock_sms:
        mock_email.delay = MagicMock()
        mock_sms.delay = MagicMock()

        await notification_service.notify_booking_confirmed(
            db_session,
            user_id=f["user"].id,
            booking_reference="ET-TEST01",
            passenger_name="Notify User",
            email="notify@test.com",
            phone="+2348012345678",
            route_name="Lagos → Ibadan",
            departure_date="15 Apr 2026",
            departure_time="08:00",
            seat_numbers="A1",
            passenger_count=1,
            currency="NGN",
            amount="5,500.00",
        )

    # Check in-app notification was created
    result = await db_session.execute(
        select(Notification).where(Notification.user_id == f["user"].id)
    )
    notifs = result.scalars().all()
    assert len(notifs) == 1
    assert notifs[0].title == "Booking Confirmed"
    assert "ET-TEST01" in notifs[0].body


@pytest.mark.asyncio
async def test_notify_booking_confirmed_dispatches_email_and_sms(db_session: AsyncSession):
    f = await _setup(db_session)

    with patch("app.tasks.email_tasks.send_templated_email") as mock_email, \
         patch("app.tasks.sms_tasks.send_sms_task") as mock_sms:
        mock_email.delay = MagicMock()
        mock_sms.delay = MagicMock()

        await notification_service.notify_booking_confirmed(
            db_session,
            user_id=f["user"].id,
            booking_reference="ET-TEST02",
            passenger_name="Test",
            email="test@example.com",
            phone="+2348099999999",
            route_name="Lagos → Ibadan",
            departure_date="15 Apr 2026",
            departure_time="08:00",
            seat_numbers="A1",
            passenger_count=1,
            currency="NGN",
            amount="5,500.00",
        )

        mock_email.delay.assert_called_once()
        call_args = mock_email.delay.call_args
        assert call_args[0][0] == "test@example.com"
        assert "ET-TEST02" in call_args[0][1]

        mock_sms.delay.assert_called_once()
        sms_args = mock_sms.delay.call_args
        assert sms_args[0][0] == "+2348099999999"
        assert "ET-TEST02" in sms_args[0][1]


@pytest.mark.asyncio
async def test_notify_skips_email_if_none(db_session: AsyncSession):
    f = await _setup(db_session)

    with patch("app.tasks.email_tasks.send_templated_email") as mock_email, \
         patch("app.tasks.sms_tasks.send_sms_task") as mock_sms:
        mock_email.delay = MagicMock()
        mock_sms.delay = MagicMock()

        await notification_service.notify_booking_confirmed(
            db_session,
            user_id=f["user"].id,
            booking_reference="ET-NOEMAIL",
            passenger_name="Test",
            email=None,
            phone=None,
            route_name="Route",
            departure_date="15 Apr 2026",
            departure_time="08:00",
            seat_numbers="A1",
            passenger_count=1,
            currency="NGN",
            amount="5,500.00",
        )

        mock_email.delay.assert_not_called()
        mock_sms.delay.assert_not_called()

    # In-app still created
    result = await db_session.execute(
        select(Notification).where(Notification.user_id == f["user"].id)
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_notify_cancellation_dispatches(db_session: AsyncSession):
    f = await _setup(db_session)

    with patch("app.tasks.email_tasks.send_templated_email") as mock_email, \
         patch("app.tasks.sms_tasks.send_sms_task") as mock_sms:
        mock_email.delay = MagicMock()
        mock_sms.delay = MagicMock()

        await notification_service.notify_booking_cancelled(
            db_session,
            user_id=f["user"].id,
            booking_reference="ET-CANCEL",
            passenger_name="Test",
            email="test@example.com",
            phone="+2348099999999",
            route_name="Lagos → Ibadan",
            departure_date="15 Apr 2026",
            reason="Change of plans",
            currency="NGN",
            refund_amount=4950,
            refund_percentage=90,
        )

        mock_email.delay.assert_called_once()
        assert "Cancelled" in mock_email.delay.call_args[0][1]

        mock_sms.delay.assert_called_once()
        assert "cancelled" in mock_sms.delay.call_args[0][1].lower()

    result = await db_session.execute(
        select(Notification).where(
            Notification.user_id == f["user"].id,
            Notification.title == "Booking Cancelled",
        )
    )
    assert result.scalar_one_or_none() is not None


# ── Integration: webhook triggers notifications ──


@pytest.mark.asyncio
async def test_webhook_triggers_booking_confirmed_notification(
    client: AsyncClient, db_session: AsyncSession
):
    f = await _setup(db_session)
    token = await _login(client)

    # Lock + book
    await client.post(
        f"/api/v1/trips/{f['trip'].id}/seats/lock",
        json={"seat_ids": [str(f["seat"].id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    book_resp = await client.post(
        "/api/v1/bookings",
        json={
            "trip_id": str(f["trip"].id),
            "passengers": [{"seat_id": str(f["seat"].id), "first_name": "P", "last_name": "U", "is_primary": True}],
            "contact_email": "notify@test.com",
            "contact_phone": "+2348012345678",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    ref = book_resp.json()["reference"]
    booking_result = await db_session.execute(
        select(Booking).where(Booking.reference == ref)
    )
    booking = booking_result.scalar_one()

    # Create payment record
    payment = Payment(
        booking_id=booking.id, user_id=f["user"].id,
        amount=5500, method="card", gateway="paystack",
        gateway_reference="webhook-notif-test",
    )
    db_session.add(payment)
    await db_session.flush()

    # Trigger webhook (mock Celery tasks to avoid actual email/SMS sends)
    with patch("app.tasks.email_tasks.send_templated_email.delay"), \
         patch("app.tasks.sms_tasks.send_sms_task.delay"):
        resp = await client.post(
            "/api/v1/payments/webhook/paystack",
            json={"event": "charge.success", "data": {"reference": "webhook-notif-test"}},
        )
    assert resp.status_code == 200

    # Check in-app notifications were created
    notif_result = await db_session.execute(
        select(Notification).where(Notification.user_id == f["user"].id)
    )
    notifs = notif_result.scalars().all()
    titles = {n.title for n in notifs}
    assert "Booking Confirmed" in titles
    assert "Payment Received" in titles


# ── Welcome email on registration ──


@pytest.mark.asyncio
async def test_registration_sends_welcome_email(client: AsyncClient, db_session: AsyncSession):
    with patch("app.tasks.email_tasks.send_welcome_email.delay") as mock_welcome:
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@test.com", "password": "securepass123",
                "first_name": "New", "last_name": "User",
            },
        )
        assert resp.status_code == 201
        mock_welcome.assert_called_once_with("newuser@test.com", "New")
