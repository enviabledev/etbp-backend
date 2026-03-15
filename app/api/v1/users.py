import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.constants import NotificationChannel
from app.dependencies import CurrentUser, DBSession
from app.schemas.common import BaseSchema, MessageResponse
from app.services import notification_service, payment_service

router = APIRouter(prefix="/users", tags=["Users"])


class NotificationResponse(BaseSchema):
    id: uuid.UUID
    channel: str
    title: str
    body: str
    data: dict | None
    is_read: bool
    sent_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    page: int
    page_size: int


# ── Notifications ──


@router.get("/me/notifications", response_model=NotificationListResponse)
async def get_my_notifications(
    db: DBSession,
    current_user: CurrentUser,
    is_read: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List current user's notifications with unread count."""
    return await notification_service.get_user_notifications(
        db, current_user.id, is_read=is_read, page=page, page_size=page_size
    )


@router.put("/me/notifications/{notification_id}/read", response_model=MessageResponse)
async def mark_notification_read(
    notification_id: uuid.UUID, db: DBSession, current_user: CurrentUser
):
    await notification_service.mark_notification_read(db, notification_id, current_user.id)
    return MessageResponse(message="Notification marked as read")


@router.put("/me/notifications/read-all", response_model=MessageResponse)
async def mark_all_notifications_read(db: DBSession, current_user: CurrentUser):
    count = await notification_service.mark_all_read(db, current_user.id)
    return MessageResponse(message=f"{count} notifications marked as read")


@router.get("/me/notifications/unread-count")
async def get_unread_count(db: DBSession, current_user: CurrentUser):
    result = await notification_service.get_user_notifications(
        db, current_user.id, is_read=False, page=1, page_size=1
    )
    return {"unread_count": result["unread_count"]}


# ── Wallet (shortcut) ──


@router.get("/me/wallet")
async def get_my_wallet(db: DBSession, current_user: CurrentUser):
    return await payment_service.get_wallet(db, current_user.id)
