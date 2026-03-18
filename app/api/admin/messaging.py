import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.message import Conversation, Message
from app.models.user import User

router = APIRouter(prefix="/messages", tags=["Admin - Messaging"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("/conversations", dependencies=[AdminUser])
async def list_all_conversations(
    db: DBSession,
    conversation_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Conversation)
    if conversation_type:
        query = query.where(Conversation.conversation_type == conversation_type)
    if status:
        query = query.where(Conversation.status == status)
    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for c in result.scalars().all():
        creator_q = await db.execute(select(User.first_name, User.last_name).where(User.id == c.created_by))
        creator = creator_q.one_or_none()
        items.append({
            "id": str(c.id), "subject": c.subject, "conversation_type": c.conversation_type,
            "status": c.status, "priority": c.priority,
            "created_by_name": f"{creator[0]} {creator[1]}" if creator else "Unknown",
            "assigned_to": str(c.assigned_to) if c.assigned_to else None,
            "last_message_preview": c.last_message_preview,
            "last_message_at": str(c.last_message_at) if c.last_message_at else None,
            "created_at": str(c.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


class AssignRequest(BaseModel):
    assigned_to: uuid.UUID


@router.post("/conversations/{conv_id}/assign", dependencies=[AdminUser])
async def assign_conversation(conv_id: uuid.UUID, data: AssignRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")
    conv.assigned_to = data.assigned_to
    conv.status = "assigned"
    # Add as participant if not already
    pids = [str(p) for p in (conv.participant_ids or [])]
    if str(data.assigned_to) not in pids:
        pids.append(str(data.assigned_to))
        conv.participant_ids = pids
    # System message
    agent_q = await db.execute(select(User.first_name, User.last_name).where(User.id == data.assigned_to))
    agent = agent_q.one_or_none()
    agent_name = f"{agent[0]} {agent[1]}" if agent else "Support"
    msg = Message(conversation_id=conv_id, sender_id=current_user.id, content=f"Assigned to {agent_name}", message_type="system")
    db.add(msg)
    conv.last_message_at = datetime.now(timezone.utc)
    conv.last_message_preview = f"Assigned to {agent_name}"
    await db.flush()
    return {"assigned": True}


class ResolveRequest(BaseModel):
    notes: str | None = None


@router.patch("/conversations/{conv_id}/resolve", dependencies=[AdminUser])
async def resolve_conversation(conv_id: uuid.UUID, data: ResolveRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Conversation not found")
    conv.status = "resolved"
    conv.resolved_at = datetime.now(timezone.utc)
    conv.resolved_by = current_user.id
    msg_text = "Conversation resolved"
    if data.notes:
        msg_text += f": {data.notes}"
    msg = Message(conversation_id=conv_id, sender_id=current_user.id, content=msg_text, message_type="system")
    db.add(msg)
    conv.last_message_at = datetime.now(timezone.utc)
    conv.last_message_preview = msg_text[:200]
    await db.flush()
    return {"resolved": True}
