import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.notification_campaign import NotificationCampaign
from app.services.audit_service import log_action

router = APIRouter(prefix="/notifications", tags=["Admin - Notifications"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateCampaignRequest(BaseModel):
    title: str = Field(..., max_length=100)
    body: str = Field(..., max_length=1000)
    channel: str = Field("push")  # push, sms, both
    target_type: str
    target_value: str | None = None
    target_description: str | None = None
    scheduled_at: datetime | None = None


class QuickSendRequest(CreateCampaignRequest):
    pass


class DisruptionAlertRequest(BaseModel):
    trip_id: uuid.UUID | None = None
    route_id: uuid.UUID | None = None
    terminal_id: uuid.UUID | None = None
    message: str
    channels: list[str] = ["push", "sms"]


@router.post("/campaigns", status_code=201, dependencies=[AdminUser])
async def create_campaign(data: CreateCampaignRequest, db: DBSession, current_user: CurrentUser):
    campaign = NotificationCampaign(
        title=data.title, body=data.body, channel=data.channel,
        target_type=data.target_type, target_value=data.target_value,
        target_description=data.target_description, scheduled_at=data.scheduled_at,
        created_by=current_user.id,
    )
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    await log_action(db, current_user.id, "create_campaign", "notification_campaign", str(campaign.id))
    return _serialize(campaign)


@router.get("/campaigns", dependencies=[AdminUser])
async def list_campaigns(
    db: DBSession,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(NotificationCampaign)
    if status:
        query = query.where(NotificationCampaign.status == status)

    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0

    query = query.order_by(NotificationCampaign.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = [_serialize(c) for c in result.scalars().all()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/campaigns/{campaign_id}", dependencies=[AdminUser])
async def get_campaign(campaign_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(NotificationCampaign).where(NotificationCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")
    return _serialize(campaign)


@router.post("/campaigns/{campaign_id}/preview", dependencies=[AdminUser])
async def preview_campaign(campaign_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(NotificationCampaign).where(NotificationCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    from app.services.notification_composer_service import preview_recipients
    return await preview_recipients(db, campaign.target_type, campaign.target_value)


@router.post("/campaigns/{campaign_id}/send", dependencies=[AdminUser])
async def send_campaign_endpoint(campaign_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(NotificationCampaign).where(NotificationCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")
    if campaign.status not in ("draft",):
        raise BadRequestError(f"Cannot send a {campaign.status} campaign")

    from app.services.notification_composer_service import send_campaign
    await send_campaign(db, campaign_id)

    await db.refresh(campaign)
    await log_action(db, current_user.id, "send_campaign", "notification_campaign", str(campaign_id))
    return _serialize(campaign)


@router.delete("/campaigns/{campaign_id}", dependencies=[AdminUser])
async def delete_campaign(campaign_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(NotificationCampaign).where(NotificationCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")
    if campaign.status != "draft":
        raise BadRequestError("Can only delete draft campaigns")
    await db.delete(campaign)
    await db.flush()
    return {"deleted": True}


@router.post("/quick-send", dependencies=[AdminUser])
async def quick_send(data: QuickSendRequest, db: DBSession, current_user: CurrentUser):
    campaign = NotificationCampaign(
        title=data.title, body=data.body, channel=data.channel,
        target_type=data.target_type, target_value=data.target_value,
        target_description=data.target_description, created_by=current_user.id,
    )
    db.add(campaign)
    await db.flush()

    from app.services.notification_composer_service import send_campaign
    await send_campaign(db, campaign.id)

    await db.refresh(campaign)
    await log_action(db, current_user.id, "quick_send_campaign", "notification_campaign", str(campaign.id))
    return _serialize(campaign)


@router.post("/disruption-alert", dependencies=[AdminUser])
async def disruption_alert(data: DisruptionAlertRequest, db: DBSession, current_user: CurrentUser):
    from app.services.notification_composer_service import resolve_recipient_user_ids
    from app.services.push_notification_service import send_push_to_multiple
    from app.models.device_token import DeviceToken

    # Determine target
    target_type = "all_customers"
    target_value = None
    if data.trip_id:
        target_type = "trip"
        target_value = str(data.trip_id)
        # Get users from this specific trip
        from app.models.booking import Booking
        user_q = await db.execute(
            select(Booking.user_id).distinct().where(
                Booking.trip_id == data.trip_id,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        user_ids = list(user_q.scalars().all())
    elif data.route_id:
        target_type = "route"
        target_value = str(data.route_id)
        user_ids = await resolve_recipient_user_ids(db, "route", target_value)
    elif data.terminal_id:
        target_type = "terminal"
        target_value = str(data.terminal_id)
        user_ids = await resolve_recipient_user_ids(db, "terminal", target_value)
    else:
        raise BadRequestError("Must specify trip_id, route_id, or terminal_id")

    # Create campaign for audit
    channel = "both" if "push" in data.channels and "sms" in data.channels else data.channels[0] if data.channels else "push"
    campaign = NotificationCampaign(
        title="Disruption Alert", body=data.message, channel=channel,
        target_type=target_type, target_value=target_value,
        target_description=f"Disruption alert ({len(user_ids)} passengers)",
        created_by=current_user.id, total_recipients=len(user_ids),
    )
    db.add(campaign)
    await db.flush()

    sent = 0
    failed = 0
    notification_data = {"type": "disruption_alert", "campaign_id": str(campaign.id)}

    if "push" in data.channels and user_ids:
        token_q = await db.execute(
            select(DeviceToken.token).where(DeviceToken.user_id.in_(user_ids), DeviceToken.is_active == True)  # noqa: E712
        )
        tokens = list(token_q.scalars().all())
        for i in range(0, len(tokens), 500):
            batch = tokens[i:i + 500]
            batch_sent = await send_push_to_multiple(batch, "Service Disruption", data.message, notification_data)
            sent += batch_sent
            failed += len(batch) - batch_sent

    if "sms" in data.channels and user_ids:
        from app.models.user import User
        phone_q = await db.execute(select(User.phone).where(User.id.in_(user_ids), User.phone.isnot(None)))
        phones = [p for p in phone_q.scalars().all() if p]
        try:
            from app.integrations.termii import TermiiClient
            from app.config import settings
            if settings.termii_api_key:
                client = TermiiClient()
                for phone in phones:
                    try:
                        await client.send_sms(phone, f"ETBP Alert: {data.message}")
                        sent += 1
                    except Exception:
                        failed += 1
        except Exception:
            failed += len(phones)

    campaign.sent_count = sent
    campaign.failed_count = failed
    campaign.status = "sent"
    campaign.sent_at = datetime.now(timezone.utc)
    await db.flush()

    await log_action(db, current_user.id, "disruption_alert", "notification_campaign", str(campaign.id))
    return {
        "campaign_id": str(campaign.id),
        "affected_passengers": len(user_ids),
        "notifications_sent": sent,
        "notifications_failed": failed,
    }


def _serialize(c: NotificationCampaign) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "body": c.body,
        "channel": c.channel,
        "target_type": c.target_type,
        "target_value": c.target_value,
        "target_description": c.target_description,
        "status": c.status,
        "total_recipients": c.total_recipients,
        "sent_count": c.sent_count,
        "failed_count": c.failed_count,
        "scheduled_at": str(c.scheduled_at) if c.scheduled_at else None,
        "sent_at": str(c.sent_at) if c.sent_at else None,
        "created_by": str(c.created_by) if c.created_by else None,
        "created_at": str(c.created_at),
    }
