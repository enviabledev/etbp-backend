import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


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


async def send_booking_confirmation(
    db: AsyncSession, user_id: uuid.UUID, booking_reference: str
) -> None:
    await create_notification(
        db,
        user_id=user_id,
        channel="in_app",
        title="Booking Confirmed",
        body=f"Your booking {booking_reference} has been confirmed.",
        data={"booking_reference": booking_reference},
    )
