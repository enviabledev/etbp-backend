import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.user import User
from app.schemas.booking import BookingDetailResponse, CreateBookingRequest
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["Agent - Bookings"])

AgentUser = Annotated[User, Depends(require_role(UserRole.AGENT, UserRole.ADMIN, UserRole.SUPER_ADMIN))]


@router.post("", status_code=201, response_model=BookingDetailResponse)
async def create_booking_for_passenger(
    data: CreateBookingRequest,
    user_id: uuid.UUID,
    db: DBSession,
    current_user: AgentUser,
):
    return await booking_service.create_booking(
        db, user_id, data, booked_by=current_user.id
    )


@router.get("/search")
async def search_bookings(
    db: DBSession,
    current_user: AgentUser,
    reference: str | None = None,
    phone: str | None = None,
):
    query = select(Booking).options(selectinload(Booking.passengers))
    if reference:
        query = query.where(Booking.reference == reference.upper())
    if phone:
        query = query.where(Booking.contact_phone == phone)
    query = query.order_by(Booking.created_at.desc()).limit(20)
    result = await db.execute(query)
    return result.scalars().all()
