import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import NotificationChannel
from app.models.notification import Notification

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def render_template(template_name: str, **context) -> str:
    template = _jinja_env.get_template(template_name)
    context.setdefault("year", datetime.now().year)
    return template.render(**context)


# ── In-app notifications ──


async def create_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    channel: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        channel=channel,
        title=title,
        body=body,
        data=data,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(notification)
    await db.flush()
    return notification


async def get_user_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    is_read: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    query = select(Notification).where(Notification.user_id == user_id)
    if is_read is not None:
        query = query.where(Notification.is_read == is_read)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar()

    query = query.order_by(Notification.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    unread_count = (
        await db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
        )
    ).scalar() or 0

    return {
        "items": items,
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "page_size": page_size,
    }


async def mark_notification_read(
    db: AsyncSession, notification_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.flush()


async def mark_all_read(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.flush()
    return result.rowcount  # type: ignore[return-value]


# ── Dispatch helpers (create in-app + enqueue email/SMS) ──


async def notify_booking_confirmed(
    db: AsyncSession,
    user_id: uuid.UUID,
    booking_reference: str,
    passenger_name: str,
    email: str | None,
    phone: str | None,
    route_name: str,
    departure_date: str,
    departure_time: str,
    seat_numbers: str,
    passenger_count: int,
    currency: str,
    amount: str,
) -> None:
    # In-app
    await create_notification(
        db, user_id,
        channel=NotificationChannel.IN_APP,
        title="Booking Confirmed",
        body=f"Your booking {booking_reference} for {route_name} on {departure_date} is confirmed.",
        data={"booking_reference": booking_reference, "event": "booking_confirmed"},
    )

    # Email via Celery
    if email:
        from app.tasks.email_tasks import send_templated_email
        html = render_template(
            "booking_confirmed.html",
            passenger_name=passenger_name,
            booking_reference=booking_reference,
            route_name=route_name,
            departure_date=departure_date,
            departure_time=departure_time,
            seat_numbers=seat_numbers,
            passenger_count=passenger_count,
            currency=currency,
            amount=amount,
        )
        send_templated_email.delay(email, f"Booking {booking_reference} Confirmed", html)

    # SMS via Celery
    if phone:
        from app.tasks.sms_tasks import send_sms_task
        msg = (
            f"ETBP: Booking {booking_reference} confirmed! "
            f"{route_name} on {departure_date} at {departure_time}. "
            f"Seats: {seat_numbers}. Arrive 30min early."
        )
        send_sms_task.delay(phone, msg)


async def notify_booking_cancelled(
    db: AsyncSession,
    user_id: uuid.UUID,
    booking_reference: str,
    passenger_name: str,
    email: str | None,
    phone: str | None,
    route_name: str,
    departure_date: str,
    reason: str,
    currency: str,
    refund_amount: float,
    refund_percentage: int,
) -> None:
    await create_notification(
        db, user_id,
        channel=NotificationChannel.IN_APP,
        title="Booking Cancelled",
        body=f"Booking {booking_reference} cancelled. Refund: {currency} {refund_amount} ({refund_percentage}%).",
        data={"booking_reference": booking_reference, "event": "booking_cancelled"},
    )

    if email:
        from app.tasks.email_tasks import send_templated_email
        html = render_template(
            "booking_cancelled.html",
            passenger_name=passenger_name,
            booking_reference=booking_reference,
            route_name=route_name,
            departure_date=departure_date,
            reason=reason or "User request",
            currency=currency,
            refund_amount=refund_amount,
            refund_percentage=refund_percentage,
        )
        send_templated_email.delay(email, f"Booking {booking_reference} Cancelled", html)

    if phone:
        from app.tasks.sms_tasks import send_sms_task
        msg = f"ETBP: Booking {booking_reference} cancelled. Refund: {currency} {refund_amount}."
        send_sms_task.delay(phone, msg)


async def notify_payment_received(
    db: AsyncSession,
    user_id: uuid.UUID,
    booking_reference: str,
    passenger_name: str,
    email: str | None,
    currency: str,
    amount: str,
    payment_method: str,
    payment_reference: str,
    payment_date: str,
) -> None:
    await create_notification(
        db, user_id,
        channel=NotificationChannel.IN_APP,
        title="Payment Received",
        body=f"Payment of {currency} {amount} received for booking {booking_reference}.",
        data={"booking_reference": booking_reference, "event": "payment_received"},
    )

    if email:
        from app.tasks.email_tasks import send_templated_email
        html = render_template(
            "payment_receipt.html",
            passenger_name=passenger_name,
            booking_reference=booking_reference,
            currency=currency,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
            payment_date=payment_date,
        )
        send_templated_email.delay(email, f"Payment Receipt — {booking_reference}", html)


async def notify_trip_reminder(
    db: AsyncSession,
    user_id: uuid.UUID,
    booking_reference: str,
    passenger_name: str,
    email: str | None,
    phone: str | None,
    route_name: str,
    departure_date: str,
    departure_time: str,
    terminal_name: str,
    seat_numbers: str,
) -> None:
    await create_notification(
        db, user_id,
        channel=NotificationChannel.IN_APP,
        title="Trip Reminder",
        body=f"Your trip {booking_reference} ({route_name}) departs tomorrow at {departure_time}.",
        data={"booking_reference": booking_reference, "event": "trip_reminder"},
    )

    if email:
        from app.tasks.email_tasks import send_templated_email
        html = render_template(
            "trip_reminder.html",
            passenger_name=passenger_name,
            booking_reference=booking_reference,
            route_name=route_name,
            departure_date=departure_date,
            departure_time=departure_time,
            terminal_name=terminal_name,
            seat_numbers=seat_numbers,
        )
        send_templated_email.delay(email, f"Trip Reminder — {booking_reference}", html)

    if phone:
        from app.tasks.sms_tasks import send_sms_task
        msg = (
            f"ETBP Reminder: Your trip {booking_reference} departs tomorrow at {departure_time} "
            f"from {terminal_name}. Arrive 30min early. Seats: {seat_numbers}."
        )
        send_sms_task.delay(phone, msg)
