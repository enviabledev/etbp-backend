import uuid

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus
from app.core.exceptions import ForbiddenError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking
from app.schemas.booking import (
    ApplyPromoRequest,
    ApplyPromoResponse,
    BookingDetailResponse,
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
    RescheduleRequest,
)
from app.services import booking_service, ticket_service

router = APIRouter(prefix="/bookings", tags=["Bookings"])


@router.post("", response_model=BookingDetailResponse, status_code=201)
async def create_booking(
    data: CreateBookingRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.create_booking(db, current_user.id, data)


@router.get("")
async def list_my_bookings(
    db: DBSession,
    current_user: CurrentUser,
    status: BookingStatus | None = None,
    upcoming: bool | None = Query(None, description="True=upcoming, False=past"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from datetime import date
    from sqlalchemy import func

    base_query = select(Booking).where(Booking.user_id == current_user.id)
    if status:
        base_query = base_query.where(Booking.status == status.value)
    if upcoming is True:
        from app.models.schedule import Trip
        base_query = base_query.join(Trip, Booking.trip_id == Trip.id).where(
            Trip.departure_date >= date.today()
        )
    elif upcoming is False:
        from app.models.schedule import Trip
        base_query = base_query.join(Trip, Booking.trip_id == Trip.id).where(
            Trip.departure_date < date.today()
        )

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar() or 0

    query = base_query.order_by(Booking.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{reference}", response_model=BookingDetailResponse)
async def get_booking_by_reference(
    reference: str, db: DBSession, current_user: CurrentUser
):
    return await booking_service.get_booking_by_reference(db, reference, current_user.id)


@router.put("/{reference}/cancel", response_model=CancelBookingResponse)
async def cancel_booking(
    reference: str,
    data: CancelBookingRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.cancel_booking(
        db, current_user.id, reference, data.reason
    )


@router.put("/{reference}/reschedule", response_model=BookingDetailResponse)
async def reschedule_booking(
    reference: str,
    data: RescheduleRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.reschedule_booking(
        db, current_user.id, reference, data.new_trip_id, data.new_seat_ids
    )


@router.get("/{reference}/ticket")
async def download_eticket(
    reference: str, db: DBSession, current_user: CurrentUser
):
    pdf_bytes = await ticket_service.generate_eticket_pdf(
        db, reference, current_user.id
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ticket-{reference}.pdf"'
        },
    )


@router.post("/{reference}/apply-promo", response_model=ApplyPromoResponse)
async def apply_promo(
    reference: str,
    data: ApplyPromoRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await booking_service.apply_promo_code(
        db, current_user.id, reference, data.promo_code
    )
