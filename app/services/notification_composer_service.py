import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.device_token import DeviceToken
from app.models.notification_campaign import NotificationCampaign
from app.models.route import Route, Terminal
from app.models.schedule import Trip
from app.models.user import User

logger = logging.getLogger(__name__)


async def resolve_recipient_user_ids(db: AsyncSession, target_type: str, target_value: str | None) -> list[uuid.UUID]:
    """Resolve target audience to a list of user IDs."""
    today = date.today()

    if target_type == "all_customers":
        result = await db.execute(select(User.id).where(User.role == "passenger", User.is_active == True))  # noqa: E712
        return list(result.scalars().all())

    elif target_type == "all_drivers":
        result = await db.execute(select(User.id).where(User.role == "driver", User.is_active == True))  # noqa: E712
        return list(result.scalars().all())

    elif target_type == "all_agents":
        result = await db.execute(select(User.id).where(User.role == "agent", User.is_active == True))  # noqa: E712
        return list(result.scalars().all())

    elif target_type == "route" and target_value:
        result = await db.execute(
            select(Booking.user_id).distinct()
            .join(Trip, Booking.trip_id == Trip.id)
            .where(
                Trip.route_id == uuid.UUID(target_value),
                Trip.departure_date >= today,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        return list(result.scalars().all())

    elif target_type == "terminal" and target_value:
        result = await db.execute(
            select(Booking.user_id).distinct()
            .join(Trip, Booking.trip_id == Trip.id)
            .join(Route, Trip.route_id == Route.id)
            .where(
                Route.origin_terminal_id == uuid.UUID(target_value),
                Trip.departure_date >= today,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        return list(result.scalars().all())

    elif target_type == "city" and target_value:
        result = await db.execute(
            select(Booking.user_id).distinct()
            .join(Trip, Booking.trip_id == Trip.id)
            .join(Route, Trip.route_id == Route.id)
            .join(Terminal, Route.origin_terminal_id == Terminal.id)
            .where(
                Terminal.city.ilike(target_value),
                Trip.departure_date >= today,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        return list(result.scalars().all())

    elif target_type == "frequent_travelers":
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Booking.user_id)
            .where(Booking.status.in_(["confirmed", "checked_in", "completed"]))
            .group_by(Booking.user_id)
            .having(func.count(Booking.id) >= 5)
        )
        return list(result.scalars().all())

    elif target_type == "new_users":
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        result = await db.execute(
            select(User.id).where(User.role == "passenger", User.created_at >= cutoff)
        )
        return list(result.scalars().all())

    elif target_type == "inactive":
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        active_users = select(Booking.user_id).where(Booking.created_at >= cutoff).subquery()
        result = await db.execute(
            select(User.id).where(
                User.role == "passenger", User.is_active == True,  # noqa: E712
                ~User.id.in_(select(active_users)),
            )
        )
        return list(result.scalars().all())

    elif target_type == "individual" and target_value:
        return [uuid.UUID(target_value)]

    return []


async def preview_recipients(db: AsyncSession, target_type: str, target_value: str | None) -> dict:
    user_ids = await resolve_recipient_user_ids(db, target_type, target_value)
    if not user_ids:
        return {"count": 0, "sample": []}

    sample_q = await db.execute(
        select(User).where(User.id.in_(user_ids[:10]))
    )
    sample = [
        {"id": str(u.id), "name": f"{u.first_name} {u.last_name}", "email": u.email, "phone": u.phone}
        for u in sample_q.scalars().all()
    ]
    return {"count": len(user_ids), "sample": sample}


async def send_campaign(db: AsyncSession, campaign_id: uuid.UUID):
    result = await db.execute(select(NotificationCampaign).where(NotificationCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        return

    campaign.status = "sending"
    await db.flush()

    user_ids = await resolve_recipient_user_ids(db, campaign.target_type, campaign.target_value)
    campaign.total_recipients = len(user_ids)

    if not user_ids:
        campaign.status = "sent"
        campaign.sent_at = datetime.now(timezone.utc)
        await db.flush()
        return

    sent = 0
    failed = 0

    # Push notifications
    if campaign.channel in ("push", "both"):
        from app.services.push_notification_service import send_push_to_multiple
        # Get all tokens for these users
        token_q = await db.execute(
            select(DeviceToken.token).where(
                DeviceToken.user_id.in_(user_ids),
                DeviceToken.is_active == True,  # noqa: E712
            )
        )
        tokens = list(token_q.scalars().all())
        data = {"type": "campaign", "campaign_id": str(campaign_id)}

        # Send in batches of 500
        for i in range(0, len(tokens), 500):
            batch = tokens[i:i + 500]
            batch_sent = await send_push_to_multiple(batch, campaign.title, campaign.body, data)
            sent += batch_sent
            failed += len(batch) - batch_sent

    # SMS
    if campaign.channel in ("sms", "both"):
        phone_q = await db.execute(
            select(User.phone).where(User.id.in_(user_ids), User.phone.isnot(None))
        )
        phones = [p for p in phone_q.scalars().all() if p]

        try:
            from app.integrations.termii import TermiiClient
            from app.config import settings
            if settings.termii_api_key:
                client = TermiiClient()
                for phone in phones:
                    try:
                        await client.send_sms(phone, f"{campaign.title}\n\n{campaign.body}")
                        sent += 1
                    except Exception:
                        failed += 1
            else:
                logger.warning("Termii not configured, skipping SMS for campaign %s", campaign_id)
        except Exception as e:
            logger.error("SMS send error: %s", e)
            failed += len(phones)

    campaign.sent_count = sent
    campaign.failed_count = failed
    campaign.status = "sent"
    campaign.sent_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("Campaign %s sent: %d sent, %d failed", campaign_id, sent, failed)
