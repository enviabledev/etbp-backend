import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, PaymentStatus, SeatStatus, UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.security import generate_booking_reference
from app.dependencies import CurrentUser, DBSession, require_role
from app.services.audit_service import log_action
from app.models.booking import Booking, BookingPassenger
from app.models.payment import Payment
from app.models.schedule import Trip, TripSeat
from app.models.user import User
from app.schemas.booking import PassengerInput

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


# ── Customer Search ──


@router.get("/customer-search", dependencies=[AdminUser])
async def search_customer_for_booking(q: str, db: DBSession):
    """Search for a customer by phone or email for walk-in booking."""
    result = await db.execute(
        select(User).where(
            or_(User.phone == q, User.email == q),
            User.role == "passenger",
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        return {"found": False, "user": None}
    return {
        "found": True,
        "user": {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "has_logged_in": getattr(user, "has_logged_in", False),
            "is_active": user.is_active,
        },
    }


# ── Create Booking for Customer ──


class AdminCreateBookingRequest(BaseModel):
    customer_phone: str
    customer_email: EmailStr | None = None
    customer_first_name: str = Field(..., max_length=100)
    customer_last_name: str = Field(..., max_length=100)
    trip_id: uuid.UUID
    passengers: list[PassengerInput] = Field(..., min_length=1)
    contact_phone: str
    contact_email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    payment_method: str = "cash"


@router.post("/create-for-customer", status_code=201, dependencies=[AdminUser])
async def create_booking_for_customer(
    data: AdminCreateBookingRequest, db: DBSession, current_user: CurrentUser
):
    """Create a booking on behalf of a walk-in customer."""

    # 1. Find or create customer
    conditions = [User.phone == data.customer_phone]
    if data.customer_email:
        conditions.append(User.email == data.customer_email)
    result = await db.execute(
        select(User).where(or_(*conditions), User.role == "passenger")
    )
    customer = result.scalar_one_or_none()
    is_new = False

    if not customer:
        is_new = True
        customer = User(
            first_name=data.customer_first_name,
            last_name=data.customer_last_name,
            phone=data.customer_phone,
            email=data.customer_email,
            role="passenger",
            is_active=True,
            has_logged_in=False,
            created_by=current_user.id,
        )
        db.add(customer)
        await db.flush()
        await db.refresh(customer)

    # 2. Validate trip and seats
    trip_result = await db.execute(select(Trip).where(Trip.id == data.trip_id))
    trip = trip_result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("Trip is not available for booking")

    seat_ids = [p.seat_id for p in data.passengers]
    seats_result = await db.execute(
        select(TripSeat).where(TripSeat.id.in_(seat_ids), TripSeat.trip_id == data.trip_id)
    )
    seats = {s.id: s for s in seats_result.scalars().all()}
    if len(seats) != len(seat_ids):
        raise BadRequestError("One or more seats not found")

    for seat in seats.values():
        if seat.status == SeatStatus.BOOKED:
            raise BadRequestError(f"Seat {seat.seat_number} is already booked")

    # 3. Create booking
    reference = generate_booking_reference()
    total_amount = sum(float(trip.price) + float(seats[p.seat_id].price_modifier) for p in data.passengers)

    booking = Booking(
        reference=reference,
        user_id=customer.id,
        trip_id=data.trip_id,
        booked_by_user_id=current_user.id,
        total_amount=total_amount,
        passenger_count=len(data.passengers),
        contact_email=data.contact_email or data.customer_email,
        contact_phone=data.contact_phone,
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_phone=data.emergency_contact_phone,
    )
    db.add(booking)
    await db.flush()

    # 4. Create passengers and mark seats booked
    for p in data.passengers:
        qr_data = f"{reference}-{seats[p.seat_id].seat_number}-{p.first_name.upper()}"
        passenger = BookingPassenger(
            booking_id=booking.id,
            seat_id=p.seat_id,
            first_name=p.first_name,
            last_name=p.last_name,
            gender=p.gender.value if p.gender else None,
            phone=p.phone,
            is_primary=p.is_primary,
            qr_code_data=qr_data,
        )
        db.add(passenger)
        seat = seats[p.seat_id]
        seat.status = SeatStatus.BOOKED
        seat.locked_by_user_id = None
        seat.locked_until = None

    trip.available_seats -= len(data.passengers)

    # 5. If cash payment, confirm immediately
    if data.payment_method == "cash":
        booking.status = BookingStatus.CONFIRMED
        payment = Payment(
            booking_id=booking.id,
            user_id=customer.id,
            amount=total_amount,
            method="cash",
            status=PaymentStatus.SUCCESSFUL.value,
            gateway="terminal",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(payment)

    await db.flush()

    await log_action(db, current_user.id, "admin_create_booking", "booking", str(booking.id), {
        "customer_id": str(customer.id),
        "customer_phone": data.customer_phone,
        "new_customer": is_new,
        "payment_method": data.payment_method,
    })

    return {
        "booking": {
            "id": str(booking.id),
            "reference": reference,
            "status": booking.status,
            "total_amount": total_amount,
        },
        "customer": {
            "id": str(customer.id),
            "is_new": is_new,
            "name": f"{customer.first_name} {customer.last_name}",
        },
    }
