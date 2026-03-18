import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.message import Conversation, Message
from app.models.user import User

router = APIRouter(prefix="/messages", tags=["Messaging"])


class CreateConversationRequest(BaseModel):
    conversation_type: str  # driver_dispatch | customer_support
    subject: str = Field(..., max_length=300)
    trip_id: uuid.UUID | None = None
    booking_id: uuid.UUID | None = None
    initial_message: str


class SendMessageRequest(BaseModel):
    content: str
    message_type: str = "text"


def _is_participant(conversation: Conversation, user_id: uuid.UUID) -> bool:
    return str(user_id) in [str(p) for p in (conversation.participant_ids or [])]


@router.post("/conversations", status_code=201)
async def create_conversation(data: CreateConversationRequest, db: DBSession, current_user: CurrentUser):
    participants = [str(current_user.id)]

    conv = Conversation(
        conversation_type=data.conversation_type,
        subject=data.subject,
        trip_id=data.trip_id,
        booking_id=data.booking_id,
        participant_ids=participants,
        created_by=current_user.id,
        last_message_at=datetime.now(timezone.utc),
        last_message_preview=data.initial_message[:200],
    )
    db.add(conv)
    await db.flush()

    msg = Message(
        conversation_id=conv.id,
        sender_id=current_user.id,
        content=data.initial_message,
    )
    db.add(msg)
    await db.flush()

    # Push to admins
    try:
        from app.services.push_notification_service import send_push_to_multiple
        from app.models.device_token import DeviceToken
        admin_q = await db.execute(select(DeviceToken.token).where(DeviceToken.app_type == "admin", DeviceToken.is_active == True))  # noqa: E712
        tokens = list(admin_q.scalars().all())
        if tokens:
            await send_push_to_multiple(tokens[:500], f"New {data.conversation_type.replace('_', ' ')}", data.subject, {"type": "new_message", "conversation_id": str(conv.id)})
    except Exception:
        pass

    return {"id": str(conv.id), "subject": conv.subject, "status": conv.status}


@router.get("/conversations")
async def list_conversations(
    db: DBSession, current_user: CurrentUser,
    conversation_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    # Get all conversations where user is participant
    query = select(Conversation)
    if conversation_type:
        query = query.where(Conversation.conversation_type == conversation_type)
    if status:
        query = query.where(Conversation.status == status)

    result = await db.execute(query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page - 1) * page_size).limit(page_size))
    items = []
    for c in result.scalars().all():
        if not _is_participant(c, current_user.id):
            continue
        # Unread count
        unread_q = await db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == c.id, Message.sender_id != current_user.id, Message.is_read == False)  # noqa: E712
        )
        items.append({
            "id": str(c.id), "subject": c.subject, "conversation_type": c.conversation_type,
            "status": c.status, "priority": c.priority,
            "last_message_preview": c.last_message_preview,
            "last_message_at": str(c.last_message_at) if c.last_message_at else None,
            "unread_count": unread_q.scalar() or 0,
        })
    return {"items": items}


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: uuid.UUID, db: DBSession, current_user: CurrentUser, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    conv_q = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = conv_q.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")
    if not _is_participant(conv, current_user.id):
        raise ForbiddenError("Not a participant")

    # Mark as read
    await db.execute(
        update(Message).where(Message.conversation_id == conv_id, Message.sender_id != current_user.id, Message.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )

    result = await db.execute(
        select(Message).options(selectinload(Message.sender))
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    messages = [
        {
            "id": str(m.id), "content": m.content, "message_type": m.message_type,
            "sender_id": str(m.sender_id),
            "sender_name": f"{m.sender.first_name} {m.sender.last_name}" if m.sender else "Unknown",
            "is_read": m.is_read, "created_at": str(m.created_at),
        }
        for m in result.scalars().all()
    ]
    messages.reverse()
    return {"messages": messages, "conversation": {"id": str(conv.id), "subject": conv.subject, "status": conv.status}}


@router.post("/conversations/{conv_id}/messages", status_code=201)
async def send_message(conv_id: uuid.UUID, data: SendMessageRequest, db: DBSession, current_user: CurrentUser):
    conv_q = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = conv_q.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")
    if not _is_participant(conv, current_user.id):
        raise ForbiddenError("Not a participant")
    if conv.status in ("closed",):
        raise BadRequestError("Conversation is closed")

    msg = Message(conversation_id=conv_id, sender_id=current_user.id, content=data.content, message_type=data.message_type)
    db.add(msg)
    conv.last_message_at = datetime.now(timezone.utc)
    conv.last_message_preview = data.content[:200]
    await db.flush()

    # Push to other participants
    try:
        from app.services.push_notification_service import send_push_to_user
        for pid in (conv.participant_ids or []):
            if str(pid) != str(current_user.id):
                await send_push_to_user(db, uuid.UUID(str(pid)), f"{current_user.first_name}", data.content[:100], {"type": "new_message", "conversation_id": str(conv_id)})
    except Exception:
        pass

    return {"id": str(msg.id), "content": msg.content, "created_at": str(msg.created_at)}


@router.get("/unread-count")
async def unread_count(db: DBSession, current_user: CurrentUser):
    # Get all conversations where user is participant, count unread
    convs_q = await db.execute(select(Conversation.id))
    conv_ids = []
    for c_row in convs_q.scalars().all():
        c_q = await db.execute(select(Conversation).where(Conversation.id == c_row))
        c = c_q.scalar_one_or_none()
        if c and _is_participant(c, current_user.id):
            conv_ids.append(c.id)

    if not conv_ids:
        return {"unread_count": 0}

    count_q = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id.in_(conv_ids),
            Message.sender_id != current_user.id,
            Message.is_read == False,  # noqa: E712
        )
    )
    return {"unread_count": count_q.scalar() or 0}
