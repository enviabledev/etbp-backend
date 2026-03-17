import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.services.audit_service import log_action
from app.models.booking import Booking
from app.models.schedule import Trip

router = APIRouter(prefix="/bookings", tags=["Admin - Bookings"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("", dependencies=[AdminUser])
async def list_all_bookings(
    db: DBSession,
    status: BookingStatus | None = None,
    trip_id: uuid.UUID | None = None,
    route_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    reference: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Booking).options(selectinload(Booking.passengers))
    if status:
        query = query.where(Booking.status == status.value)
    if trip_id:
        query = query.where(Booking.trip_id == trip_id)
    if route_id:
        query = query.join(Trip, Booking.trip_id == Trip.id).where(Trip.route_id == route_id)
    if user_id:
        query = query.where(Booking.user_id == user_id)
    if reference:
        query = query.where(Booking.reference.ilike(f"%{reference.upper()}%"))
    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar()

    query = query.order_by(Booking.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    bookings = result.scalars().all()

    return {
        "items": bookings,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{booking_id}", dependencies=[AdminUser])
async def get_booking(booking_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers),
            selectinload(Booking.payments),
            selectinload(Booking.user),
        )
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    return booking


@router.put("/{booking_id}/status", dependencies=[AdminUser])
async def update_booking_status(
    booking_id: uuid.UUID, status: BookingStatus, db: DBSession, current_user: CurrentUser
):
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")

    old_status = booking.status
    booking.status = status.value
    await db.flush()
    await log_action(db, current_user.id, "update_booking_status", "booking", str(booking_id), {"new_status": status.value})
    return {
        "id": str(booking.id),
        "reference": booking.reference,
        "old_status": old_status,
        "new_status": booking.status,
    }


@router.put("/{booking_id}/check-in", dependencies=[AdminUser])
async def check_in_booking(booking_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(Booking).options(selectinload(Booking.passengers)).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status != BookingStatus.CONFIRMED:
        raise BadRequestError("Only confirmed bookings can be checked in")

    from datetime import datetime, timezone
    booking.status = BookingStatus.CHECKED_IN
    booking.checked_in_at = datetime.now(timezone.utc)
    for passenger in booking.passengers:
        passenger.checked_in = True
    await db.flush()
    await log_action(db, current_user.id, "check_in_booking", "booking", str(booking_id))

    return {
        "id": str(booking.id),
        "reference": booking.reference,
        "status": booking.status,
        "checked_in_at": str(booking.checked_in_at),
        "passengers_checked_in": len(booking.passengers),
    }
