import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.notification import SupportTicket

router = APIRouter(prefix="/support", tags=["Support"])


class CreateTicketRequest(BaseModel):
    booking_id: uuid.UUID | None = None
    category: str = Field(..., max_length=100)
    subject: str = Field(..., max_length=255)
    description: str


@router.post("", status_code=201)
async def create_ticket(
    data: CreateTicketRequest, db: DBSession, current_user: CurrentUser
):
    ticket = SupportTicket(
        user_id=current_user.id,
        booking_id=data.booking_id,
        category=data.category,
        subject=data.subject,
        description=data.description,
    )
    db.add(ticket)
    await db.flush()
    await db.refresh(ticket)
    return {"id": str(ticket.id), "status": ticket.status, "message": "Ticket created"}


@router.get("")
async def list_my_tickets(
    db: DBSession,
    current_user: CurrentUser,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(SupportTicket).where(SupportTicket.user_id == current_user.id)
    if status:
        query = query.where(SupportTicket.status == status)
    query = query.order_by(SupportTicket.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id, SupportTicket.user_id == current_user.id
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundError("Ticket not found")
    return ticket
