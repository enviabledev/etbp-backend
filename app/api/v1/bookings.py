import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus
from app.core.exceptions import ForbiddenError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking
from app.schemas.booking import (
    BookingDetailResponse,
    BookingResponse,
    CancelBookingRequest,
    CreateBookingRequest,
)
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["Bookings"])


@router.post("", response_model=BookingDetailResponse, status_code=201)
async def create_booking(
    data: CreateBookingRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.create_booking(db, current_user.id, data)


@router.get("", response_model=list[BookingResponse])
async def list_my_bookings(
    db: DBSession,
    current_user: CurrentUser,
    status: BookingStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = (
        select(Booking)
        .where(Booking.user_id == current_user.id)
        .order_by(Booking.created_at.desc())
    )
    if status:
        query = query.where(Booking.status == status.value)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{booking_id}", response_model=BookingDetailResponse)
async def get_booking(booking_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.user_id != current_user.id:
        raise ForbiddenError("Access denied")
    return booking


@router.get("/reference/{reference}", response_model=BookingDetailResponse)
async def get_booking_by_reference(
    reference: str, db: DBSession, current_user: CurrentUser
):
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers))
        .where(Booking.reference == reference.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.user_id != current_user.id:
        raise ForbiddenError("Access denied")
    return booking


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: uuid.UUID,
    data: CancelBookingRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.cancel_booking(db, current_user.id, booking_id, data.reason)
