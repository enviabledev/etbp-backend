import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, SeatStatus
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.security import generate_booking_reference
from app.models.booking import Booking, BookingPassenger
from app.models.schedule import Trip, TripSeat
from app.schemas.booking import BookingDetailResponse, CreateBookingRequest


async def create_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: CreateBookingRequest,
    booked_by: uuid.UUID | None = None,
) -> BookingDetailResponse:
    # Verify trip exists and has availability
    trip_result = await db.execute(select(Trip).where(Trip.id == data.trip_id))
    trip = trip_result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("Trip is not available for booking")
    if trip.available_seats < len(data.passengers):
        raise BadRequestError("Not enough seats available")

    # Verify all seats are available or locked by this user
    seat_ids = [p.seat_id for p in data.passengers]
    seats_result = await db.execute(
        select(TripSeat).where(
            TripSeat.id.in_(seat_ids), TripSeat.trip_id == data.trip_id
        )
    )
    seats = {s.id: s for s in seats_result.scalars().all()}

    if len(seats) != len(seat_ids):
        raise BadRequestError("One or more seats not found")

    for seat in seats.values():
        if seat.status == SeatStatus.BOOKED:
            raise BadRequestError(f"Seat {seat.seat_number} is already booked")
        if seat.status == SeatStatus.LOCKED and seat.locked_by_user_id != user_id:
            raise BadRequestError(f"Seat {seat.seat_number} is locked by another user")

    # Generate unique reference
    reference = generate_booking_reference()

    # Calculate total (price + seat modifiers)
    total_amount = sum(float(trip.price) + float(seats[p.seat_id].price_modifier) for p in data.passengers)

    booking = Booking(
        reference=reference,
        user_id=user_id,
        trip_id=data.trip_id,
        booked_by_user_id=booked_by,
        total_amount=total_amount,
        passenger_count=len(data.passengers),
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_phone=data.emergency_contact_phone,
        special_requests=data.special_requests,
    )
    db.add(booking)
    await db.flush()

    # Create passengers and mark seats as booked
    for p in data.passengers:
        qr_data = f"{reference}-{p.seat_id}"
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

    # Update available seats on trip
    trip.available_seats -= len(data.passengers)

    await db.flush()

    # Reload with passengers
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers))
        .where(Booking.id == booking.id)
    )
    return BookingDetailResponse.model_validate(result.scalar_one())


async def cancel_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    booking_id: uuid.UUID,
    reason: str | None = None,
) -> Booking:
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.user_id != user_id:
        raise ForbiddenError("Access denied")
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.COMPLETED):
        raise BadRequestError(f"Cannot cancel a {booking.status} booking")

    booking.status = BookingStatus.CANCELLED
    booking.cancellation_reason = reason
    booking.cancelled_at = datetime.now(timezone.utc)

    # Release seats
    seat_ids = [p.seat_id for p in booking.passengers]
    if seat_ids:
        await db.execute(
            update(TripSeat)
            .where(TripSeat.id.in_(seat_ids))
            .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
        )

        # Restore available seats on trip
        trip_result = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
        trip = trip_result.scalar_one()
        trip.available_seats += len(seat_ids)

    await db.flush()
    return booking
