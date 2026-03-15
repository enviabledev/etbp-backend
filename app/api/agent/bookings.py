import uuid
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import (
    BookingStatus,
    GenderType,
    PaymentMethod,
    PaymentStatus,
    UserRole,
)
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.booking import Booking, BookingPassenger
from app.models.payment import Payment
from app.models.schedule import Trip, TripSeat
from app.models.user import User
from app.schemas.booking import BookingDetailResponse, CreateBookingRequest
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["Agent - Bookings"])

AgentUser = Annotated[User, Depends(require_role(UserRole.AGENT, UserRole.ADMIN, UserRole.SUPER_ADMIN))]


# ── Schemas ──


class AgentPassengerInput(BaseModel):
    seat_id: uuid.UUID
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    gender: GenderType | None = None
    phone: str | None = Field(None, max_length=20)
    is_primary: bool = False


class AgentBookingRequest(BaseModel):
    trip_id: uuid.UUID
    passengers: list[AgentPassengerInput] = Field(..., min_length=1)
    contact_phone: str | None = Field(None, max_length=20)
    contact_email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    special_requests: str | None = None
    payment_method: PaymentMethod = PaymentMethod.CASH
    collect_payment: bool = Field(True, description="Auto-confirm with cash payment")


class CheckInRequest(BaseModel):
    qr_code: str | None = None
    booking_reference: str | None = None
    passenger_id: uuid.UUID | None = None


class ManifestPassenger(BaseModel):
    model_config = {"from_attributes": True}

    passenger_id: str
    booking_reference: str
    first_name: str
    last_name: str
    gender: str | None
    phone: str | None
    seat_number: str
    checked_in: bool


# ── Create Booking (walk-in, skip lock) ──


@router.post("", status_code=201, response_model=BookingDetailResponse)
async def create_agent_booking(
    data: AgentBookingRequest,
    db: DBSession,
    current_user: AgentUser,
):
    """Create booking for walk-in passenger. Skips seat lock requirement.
    If collect_payment=true and method=cash, auto-confirms the booking."""
    # Find or create a guest user for the primary passenger
    primary = next((p for p in data.passengers if p.is_primary), data.passengers[0])

    guest_user = None
    if data.contact_phone:
        result = await db.execute(
            select(User).where(User.phone == data.contact_phone)
        )
        guest_user = result.scalar_one_or_none()

    if not guest_user:
        from app.core.security import hash_password
        import secrets
        guest_user = User(
            phone=data.contact_phone,
            email=data.contact_email,
            first_name=primary.first_name,
            last_name=primary.last_name,
            password_hash=hash_password(secrets.token_urlsafe(16)),
            role=UserRole.PASSENGER,
        )
        db.add(guest_user)
        await db.flush()

    booking_request = CreateBookingRequest(
        trip_id=data.trip_id,
        passengers=[
            {
                "seat_id": p.seat_id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "gender": p.gender,
                "phone": p.phone,
                "is_primary": p.is_primary,
            }
            for p in data.passengers
        ],
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_phone=data.emergency_contact_phone,
        special_requests=data.special_requests,
    )

    booking_detail = await booking_service.create_booking(
        db, guest_user.id, booking_request,
        booked_by=current_user.id,
        skip_lock_check=True,
    )

    # If agent collects cash payment, auto-confirm
    if data.collect_payment:
        booking_result = await db.execute(
            select(Booking).where(Booking.id == booking_detail.id)
        )
        booking = booking_result.scalar_one()

        payment = Payment(
            booking_id=booking.id,
            user_id=guest_user.id,
            amount=float(booking.total_amount),
            method=data.payment_method.value,
            status=PaymentStatus.SUCCESSFUL,
            gateway="agent_portal",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        booking.status = BookingStatus.CONFIRMED
        await db.flush()

        # Reload
        result = await db.execute(
            select(Booking)
            .options(selectinload(Booking.passengers))
            .where(Booking.id == booking.id)
        )
        return BookingDetailResponse.model_validate(result.scalar_one())

    return booking_detail


# ── Search ──


@router.get("/search")
async def search_bookings(
    db: DBSession,
    current_user: AgentUser,
    reference: str | None = Query(None, description="Booking reference (partial)"),
    phone: str | None = Query(None, description="Contact phone"),
    passenger_name: str | None = Query(None, description="Passenger first or last name"),
    trip_id: uuid.UUID | None = None,
    date: date | None = Query(None, description="Booking date"),
    status: BookingStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Search bookings by reference, phone, passenger name, trip, date, or status."""
    query = select(Booking).options(selectinload(Booking.passengers))

    if reference:
        query = query.where(Booking.reference.ilike(f"%{reference.upper()}%"))
    if phone:
        query = query.where(Booking.contact_phone.ilike(f"%{phone}%"))
    if passenger_name:
        query = query.where(
            Booking.id.in_(
                select(BookingPassenger.booking_id).where(
                    BookingPassenger.first_name.ilike(f"%{passenger_name}%")
                    | BookingPassenger.last_name.ilike(f"%{passenger_name}%")
                )
            )
        )
    if trip_id:
        query = query.where(Booking.trip_id == trip_id)
    if date:
        query = query.where(func.date(Booking.created_at) == date)
    if status:
        query = query.where(Booking.status == status.value)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(Booking.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)

    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


# ── Check-in ──


@router.post("/check-in")
async def check_in_passenger(
    data: CheckInRequest,
    db: DBSession,
    current_user: AgentUser,
):
    """Check in a passenger by QR code, booking reference, or passenger ID."""
    if not any([data.qr_code, data.booking_reference, data.passenger_id]):
        raise BadRequestError("Provide qr_code, booking_reference, or passenger_id")

    if data.passenger_id:
        result = await db.execute(
            select(BookingPassenger)
            .options(selectinload(BookingPassenger.booking))
            .where(BookingPassenger.id == data.passenger_id)
        )
        passenger = result.scalar_one_or_none()
        if not passenger:
            raise NotFoundError("Passenger not found")

    elif data.qr_code:
        result = await db.execute(
            select(BookingPassenger)
            .options(selectinload(BookingPassenger.booking))
            .where(BookingPassenger.qr_code_data == data.qr_code)
        )
        passenger = result.scalar_one_or_none()
        if not passenger:
            raise NotFoundError("Invalid QR code")

    else:
        booking_result = await db.execute(
            select(Booking)
            .options(selectinload(Booking.passengers))
            .where(Booking.reference == data.booking_reference.upper())
        )
        booking = booking_result.scalar_one_or_none()
        if not booking:
            raise NotFoundError("Booking not found")
        if booking.status != BookingStatus.CONFIRMED:
            raise BadRequestError(f"Cannot check in a {booking.status} booking")

        # Check in all passengers
        checked = 0
        for p in booking.passengers:
            if not p.checked_in:
                p.checked_in = True
                checked += 1

        booking.status = BookingStatus.CHECKED_IN
        booking.checked_in_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "booking_reference": booking.reference,
            "passengers_checked_in": checked,
            "status": booking.status,
        }

    # Single passenger check-in
    booking = passenger.booking
    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN):
        raise BadRequestError(f"Cannot check in — booking is {booking.status}")
    if passenger.checked_in:
        raise BadRequestError(f"{passenger.first_name} {passenger.last_name} is already checked in")

    passenger.checked_in = True

    # If all passengers checked in, update booking status
    all_passengers_result = await db.execute(
        select(BookingPassenger).where(BookingPassenger.booking_id == booking.id)
    )
    all_passengers = all_passengers_result.scalars().all()
    if all(p.checked_in for p in all_passengers):
        booking.status = BookingStatus.CHECKED_IN
        booking.checked_in_at = datetime.now(timezone.utc)

    await db.flush()

    return {
        "booking_reference": booking.reference,
        "passenger": f"{passenger.first_name} {passenger.last_name}",
        "seat": passenger.qr_code_data.split("-")[1] if passenger.qr_code_data and "-" in passenger.qr_code_data else "N/A",
        "checked_in": True,
        "booking_status": booking.status,
    }


# ── Trip Manifest ──


@router.get("/manifest/{trip_id}")
async def get_trip_manifest(
    trip_id: uuid.UUID,
    db: DBSession,
    current_user: AgentUser,
):
    """Get passenger manifest for a trip — all passengers with seat/check-in info."""
    trip_result = await db.execute(
        select(Trip)
        .options(selectinload(Trip.route))
        .where(Trip.id == trip_id)
    )
    trip = trip_result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")

    bookings_result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers).selectinload(BookingPassenger.seat))
        .where(
            Booking.trip_id == trip_id,
            Booking.status.in_([
                BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN,
            ]),
        )
        .order_by(Booking.created_at)
    )
    bookings = bookings_result.scalars().all()

    passengers = []
    for booking in bookings:
        for p in booking.passengers:
            passengers.append({
                "passenger_id": str(p.id),
                "booking_reference": booking.reference,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "gender": p.gender,
                "phone": p.phone,
                "seat_number": p.seat.seat_number if p.seat else "N/A",
                "checked_in": p.checked_in,
            })

    passengers.sort(key=lambda x: x["seat_number"])

    return {
        "trip_id": str(trip.id),
        "route": trip.route.name if trip.route else "N/A",
        "departure_date": str(trip.departure_date),
        "departure_time": str(trip.departure_time),
        "total_seats": trip.total_seats,
        "booked_passengers": len(passengers),
        "checked_in_count": sum(1 for p in passengers if p["checked_in"]),
        "passengers": passengers,
    }
