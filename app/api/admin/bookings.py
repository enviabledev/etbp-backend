import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.booking import Booking

router = APIRouter(prefix="/bookings", tags=["Admin - Bookings"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("", dependencies=[AdminUser])
async def list_all_bookings(
    db: DBSession,
    status: BookingStatus | None = None,
    trip_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Booking).options(selectinload(Booking.passengers))
    if status:
        query = query.where(Booking.status == status.value)
    if trip_id:
        query = query.where(Booking.trip_id == trip_id)
    query = query.order_by(Booking.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/{booking_id}/status", dependencies=[AdminUser])
async def update_booking_status(
    booking_id: uuid.UUID, status: BookingStatus, db: DBSession
):
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    booking.status = status.value
    await db.flush()
    return {"id": str(booking.id), "status": booking.status}
