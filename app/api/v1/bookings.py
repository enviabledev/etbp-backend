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
    AddLuggageRequest,
    ApplyPromoRequest,
    ApplyPromoResponse,
    BookingDetailResponse,
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CreateBookingRequest,
    RescheduleRequest,
    TransferRequest,
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

    from app.models.schedule import Trip
    from app.models.route import Route

    base_query = (
        select(Booking)
        .options(
            selectinload(Booking.passengers),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.origin_terminal),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.destination_terminal),
        )
        .where(Booking.user_id == current_user.id)
    )
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


@router.get("/{reference}/reschedule-options")
async def get_reschedule_options_endpoint(
    reference: str, db: DBSession, current_user: CurrentUser
):
    return await booking_service.get_reschedule_options(db, current_user.id, reference)


@router.post("/{reference}/transfer")
async def transfer_booking_endpoint(
    reference: str, data: TransferRequest, db: DBSession, current_user: CurrentUser
):
    return await booking_service.transfer_booking(
        db, current_user.id, reference,
        data.recipient_phone, data.recipient_name, data.recipient_email,
    )


@router.post("/{reference}/add-luggage")
async def add_luggage_endpoint(
    reference: str, data: AddLuggageRequest, db: DBSession, current_user: CurrentUser
):
    return await booking_service.add_luggage(
        db, current_user.id, reference, data.quantity, data.payment_method,
    )


@router.get("/{reference}/addons")
async def get_addons_endpoint(
    reference: str, db: DBSession, current_user: CurrentUser
):
    return await booking_service.get_booking_addons(db, reference, current_user.id)


@router.get("/{reference}/calendar")
async def get_calendar_event(reference: str, db: DBSession, current_user: CurrentUser):
    """Download .ics calendar file for this booking."""
    from datetime import timedelta

    booking = await booking_service.get_booking_by_reference(db, reference, current_user.id)
    if booking.status not in ("confirmed", "checked_in"):
        from app.core.exceptions import BadRequestError
        raise BadRequestError("Calendar events only available for confirmed bookings")

    trip = booking.trip
    if not trip:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Trip not found")

    route = trip.route if hasattr(trip, 'route') and trip.route else None
    origin = route.origin_terminal if route else None
    destination = route.destination_terminal if route else None

    # Build datetime strings
    from datetime import datetime, timezone
    dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    duration = timedelta(minutes=route.estimated_duration_minutes) if route and route.estimated_duration_minutes else timedelta(hours=8)
    arr_dt = dep_dt + duration

    dt_fmt = "%Y%m%dT%H%M%SZ"
    dtstart = dep_dt.strftime(dt_fmt)
    dtend = arr_dt.strftime(dt_fmt)

    route_name = route.name if route else "Bus Trip"
    summary = f"Bus Trip: {route_name}"

    # Passengers
    passengers_str = ", ".join(
        f"{p.first_name} {p.last_name} (Seat {p.seat.seat_number if p.seat else '?'})"
        for p in booking.passengers
    ) if booking.passengers else ""

    description = f"Booking Ref: {booking.reference}\\nPassengers: {passengers_str}\\nPayment: ₦{float(booking.total_amount):,.0f}\\n\\nShow your e-ticket QR code at boarding."

    location = ""
    geo_line = ""
    if origin:
        location = f"{origin.name}"
        if origin.address:
            location += f", {origin.address}"
        location += f", {origin.city}"
        if origin.latitude and origin.longitude:
            geo_line = f"GEO:{origin.latitude};{origin.longitude}"

    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Enviable Transport//ETBP//EN
BEGIN:VEVENT
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
DESCRIPTION:{description}
LOCATION:{location}
{geo_line}
STATUS:CONFIRMED
BEGIN:VALARM
TRIGGER:-PT1H
ACTION:DISPLAY
DESCRIPTION:Your bus departs in 1 hour!
END:VALARM
BEGIN:VALARM
TRIGGER:-PT24H
ACTION:DISPLAY
DESCRIPTION:Your bus trip is tomorrow!
END:VALARM
END:VEVENT
END:VCALENDAR""".strip()

    # Clean up empty lines from missing GEO
    ics = "\n".join(line for line in ics.split("\n") if line.strip())

    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="trip-{reference}.ics"'},
    )


@router.get("/{reference}/calendar-data")
async def get_calendar_data(reference: str, db: DBSession, current_user: CurrentUser):
    """Get calendar event data as JSON (for mobile native calendar APIs)."""
    from datetime import timedelta, datetime, timezone

    booking = await booking_service.get_booking_by_reference(db, reference, current_user.id)
    trip = booking.trip
    route = trip.route if trip else None
    origin = route.origin_terminal if route else None

    dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc) if trip else None
    duration = timedelta(minutes=route.estimated_duration_minutes) if route and route.estimated_duration_minutes else timedelta(hours=8)
    arr_dt = dep_dt + duration if dep_dt else None

    route_name = route.name if route else "Bus Trip"
    passengers_str = ", ".join(
        f"{p.first_name} {p.last_name} (Seat {p.seat.seat_number if p.seat else '?'})"
        for p in booking.passengers
    ) if booking.passengers else ""

    location = ""
    if origin:
        location = origin.name
        if origin.address:
            location += f", {origin.address}"
        location += f", {origin.city}"

    return {
        "title": f"Bus Trip: {route_name}",
        "description": f"Booking Ref: {booking.reference}\nPassengers: {passengers_str}",
        "location": location,
        "start_time": dep_dt.isoformat() if dep_dt else None,
        "end_time": arr_dt.isoformat() if arr_dt else None,
        "reminders": [1440, 60],
        "latitude": float(origin.latitude) if origin and origin.latitude else None,
        "longitude": float(origin.longitude) if origin and origin.longitude else None,
    }


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


@router.get("/unpaid")
async def list_unpaid_bookings(db: DBSession, current_user: CurrentUser):
    """List bookings that haven't been paid yet (pay-at-terminal)."""
    from sqlalchemy import func
    from app.models.payment import Payment
    from app.models.schedule import Trip
    from app.models.route import Route

    # Bookings with no successful payment
    subq = select(Payment.booking_id).where(Payment.status.in_(["successful", "completed"])).subquery()
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.trip).selectinload(Trip.route))
        .where(
            Booking.user_id == current_user.id,
            Booking.status == BookingStatus.PENDING.value,
            ~Booking.id.in_(select(subq)),
        )
        .order_by(Booking.created_at.desc())
        .limit(10)
    )
    bookings = result.scalars().all()
    return {
        "items": [{
            "id": str(b.id),
            "booking_ref": b.reference,
            "route_name": b.trip.route.name if b.trip and b.trip.route else None,
            "departure_date": str(b.trip.departure_date) if b.trip else None,
            "departure_time": str(b.trip.departure_time) if b.trip else None,
            "amount_due": float(b.total_amount),
        } for b in bookings],
    }
