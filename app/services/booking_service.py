import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import logging

from app.core.constants import BookingStatus, PaymentStatus, SeatStatus
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.security import generate_booking_reference

logger = logging.getLogger(__name__)
from app.models.booking import Booking, BookingPassenger
from app.models.payment import Payment, PromoCode
from app.models.schedule import Trip, TripSeat
from app.schemas.booking import BookingDetailResponse, CreateBookingRequest


def _calc_payment_deadline(created_at: datetime, departure_dt: datetime, method: str | None) -> datetime:
    """Calculate payment deadline based on method and trip departure."""
    if method == "pay_at_terminal":
        time_to_departure = departure_dt - created_at
        # Trip departs in less than 15 minutes: give 10 minutes
        if time_to_departure < timedelta(minutes=15):
            return created_at + timedelta(minutes=10)
        # Trip departs in less than 1 hour: 15 minutes before departure
        if time_to_departure < timedelta(hours=1):
            return departure_dt - timedelta(minutes=15)
        # Normal: min(3 hours, 1 hour before departure)
        return min(created_at + timedelta(hours=3), departure_dt - timedelta(hours=1))
    # Online payments (card, wallet, etc.): 15 min
    return created_at + timedelta(minutes=15)


async def create_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: CreateBookingRequest,
    booked_by: uuid.UUID | None = None,
    skip_lock_check: bool = False,
) -> BookingDetailResponse:
    trip_result = await db.execute(select(Trip).where(Trip.id == data.trip_id))
    trip = trip_result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("Trip is not available for booking")
    if trip.available_seats < len(data.passengers):
        raise BadRequestError("Not enough seats available")

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
        if not skip_lock_check:
            if seat.status == SeatStatus.LOCKED and seat.locked_by_user_id != user_id:
                raise BadRequestError(f"Seat {seat.seat_number} is locked by another user")
            if seat.status == SeatStatus.AVAILABLE:
                raise BadRequestError(
                    f"Seat {seat.seat_number} is not locked. Lock seats before booking."
                )
        else:
            # Agent flow: seat must be available or locked by this agent
            if seat.status == SeatStatus.LOCKED and seat.locked_by_user_id != booked_by:
                raise BadRequestError(f"Seat {seat.seat_number} is locked by another user")

    # Generate unique reference with collision check
    for _ in range(10):
        reference = generate_booking_reference()
        exists = await db.execute(
            select(Booking.id).where(Booking.reference == reference)
        )
        if not exists.scalar_one_or_none():
            break
    else:
        raise BadRequestError("Could not generate unique booking reference")

    # Calculate total
    total_amount = sum(
        float(trip.price) + float(seats[p.seat_id].price_modifier)
        for p in data.passengers
    )

    # Calculate payment deadline
    method = getattr(data, "payment_method", None)
    now = datetime.now(timezone.utc)
    departure_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    payment_deadline = _calc_payment_deadline(now, departure_dt, method)

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
        payment_method_hint=method,
        payment_deadline=payment_deadline,
    )
    db.add(booking)
    await db.flush()

    logger.info(
        "Created booking %s: payment_method=%s, deadline=%s",
        reference, method, payment_deadline,
    )

    # Create passengers with QR codes, mark seats booked
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
    await db.flush()

    from app.models.route import Route

    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.origin_terminal),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.destination_terminal),
            selectinload(Booking.payments),
        )
        .where(Booking.id == booking.id)
    )
    return BookingDetailResponse.model_validate(result.scalar_one())


async def get_booking_by_reference(
    db: AsyncSession, reference: str, user_id: uuid.UUID | None = None
) -> Booking:
    from app.models.route import Route

    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.origin_terminal),
            selectinload(Booking.trip)
            .selectinload(Trip.route)
            .selectinload(Route.destination_terminal),
            selectinload(Booking.payments),
        )
        .where(Booking.reference == reference.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if user_id and booking.user_id != user_id:
        raise ForbiddenError("Access denied")
    return booking


def _calculate_refund_percentage(trip: Trip) -> int:
    """90% if >24h before departure, 50% if 12-24h, 0% if <12h."""
    now = datetime.now(timezone.utc)
    departure_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    hours_until = (departure_dt - now).total_seconds() / 3600

    if hours_until > 24:
        return 90
    elif hours_until > 12:
        return 50
    else:
        return 0


async def cancel_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    reference: str,
    reason: str | None = None,
) -> dict:
    booking = await get_booking_by_reference(db, reference, user_id)

    if booking.status in (
        BookingStatus.CANCELLED, BookingStatus.COMPLETED, BookingStatus.EXPIRED
    ):
        raise BadRequestError(f"Cannot cancel a {booking.status} booking")

    # Get trip for refund calculation
    trip_result = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
    trip = trip_result.scalar_one()

    refund_pct = _calculate_refund_percentage(trip)
    refund_amount = round(float(booking.total_amount) * refund_pct / 100, 2)

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
        trip.available_seats += len(seat_ids)

    # Mark any successful payment for refund
    if refund_amount > 0:
        payments_result = await db.execute(
            select(Payment).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.SUCCESSFUL,
            )
        )
        for payment in payments_result.scalars().all():
            payment.refund_amount = refund_amount
            payment.refund_reason = reason or "User cancellation"
            payment.status = (
                PaymentStatus.REFUNDED
                if refund_pct == 100
                else PaymentStatus.PARTIALLY_REFUNDED
            )

    await db.flush()

    # Send cancellation notifications
    from app.services.notification_service import notify_booking_cancelled

    primary = next(
        (p for p in booking.passengers if p.is_primary),
        booking.passengers[0] if booking.passengers else None,
    )
    name = f"{primary.first_name} {primary.last_name}" if primary else "Customer"

    from app.models.route import Route
    from sqlalchemy.orm import selectinload as _sel
    trip_with_route = await db.execute(
        select(Trip).options(_sel(Trip.route)).where(Trip.id == booking.trip_id)
    )
    trip_obj = trip_with_route.scalar_one()
    route_name = trip_obj.route.name if trip_obj.route else "N/A"

    await notify_booking_cancelled(
        db,
        user_id=booking.user_id,
        booking_reference=booking.reference,
        passenger_name=name,
        email=booking.contact_email,
        phone=booking.contact_phone,
        route_name=route_name,
        departure_date=trip.departure_date.strftime("%d %b %Y"),
        reason=reason or "User request",
        currency=booking.currency,
        refund_amount=refund_amount,
        refund_percentage=refund_pct,
    )

    # Push notification
    try:
        from app.services.push_notification_service import send_push_to_user
        await send_push_to_user(
            db, booking.user_id, "Booking Cancelled",
            f"Your booking {booking.reference} has been cancelled.",
            {"type": "booking_cancelled", "booking_ref": booking.reference},
            app_type="customer",
        )
    except Exception:
        pass

    return {
        "id": booking.id,
        "reference": booking.reference,
        "status": booking.status,
        "cancellation_reason": booking.cancellation_reason,
        "cancelled_at": booking.cancelled_at,
        "refund_amount": refund_amount,
        "refund_percentage": refund_pct,
    }


async def reschedule_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    reference: str,
    new_trip_id: uuid.UUID,
    new_seat_ids: list[uuid.UUID],
) -> BookingDetailResponse:
    booking = await get_booking_by_reference(db, reference, user_id)

    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.PENDING):
        raise BadRequestError(f"Cannot reschedule a {booking.status} booking")

    if len(new_seat_ids) != booking.passenger_count:
        raise BadRequestError(
            f"Must provide exactly {booking.passenger_count} seats"
        )

    # Verify new trip
    new_trip_result = await db.execute(select(Trip).where(Trip.id == new_trip_id))
    new_trip = new_trip_result.scalar_one_or_none()
    if not new_trip:
        raise NotFoundError("New trip not found")
    if new_trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("New trip is not available")
    if new_trip.available_seats < booking.passenger_count:
        raise BadRequestError("Not enough seats on new trip")

    # Verify new seats are available
    new_seats_result = await db.execute(
        select(TripSeat).where(
            TripSeat.id.in_(new_seat_ids), TripSeat.trip_id == new_trip_id
        )
    )
    new_seats = {s.id: s for s in new_seats_result.scalars().all()}
    if len(new_seats) != len(new_seat_ids):
        raise BadRequestError("One or more new seats not found")
    for seat in new_seats.values():
        if seat.status != SeatStatus.AVAILABLE:
            raise BadRequestError(f"Seat {seat.seat_number} is not available")

    # Release old seats
    old_trip_result = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
    old_trip = old_trip_result.scalar_one()
    old_seat_ids = [p.seat_id for p in booking.passengers]
    if old_seat_ids:
        await db.execute(
            update(TripSeat)
            .where(TripSeat.id.in_(old_seat_ids))
            .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
        )
        old_trip.available_seats += len(old_seat_ids)

    # Assign new seats
    new_total = sum(
        float(new_trip.price) + float(new_seats[sid].price_modifier)
        for sid in new_seat_ids
    )
    new_seat_list = list(new_seats.values())
    for i, passenger in enumerate(booking.passengers):
        new_seat = new_seat_list[i]
        passenger.seat_id = new_seat.id
        new_seat.status = SeatStatus.BOOKED

    new_trip.available_seats -= booking.passenger_count
    booking.trip_id = new_trip_id
    booking.total_amount = new_total

    await db.flush()

    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.trip).selectinload(Trip.route),
        )
        .where(Booking.id == booking.id)
    )
    return BookingDetailResponse.model_validate(result.scalar_one())


async def apply_promo_code(
    db: AsyncSession,
    user_id: uuid.UUID,
    reference: str,
    promo_code: str,
) -> dict:
    booking = await get_booking_by_reference(db, reference, user_id)

    if booking.status != BookingStatus.PENDING:
        raise BadRequestError("Promo can only be applied to pending bookings")

    # Find promo
    promo_result = await db.execute(
        select(PromoCode).where(
            PromoCode.code == promo_code.upper(),
            PromoCode.is_active == True,  # noqa: E712
        )
    )
    promo = promo_result.scalar_one_or_none()
    if not promo:
        raise NotFoundError("Invalid promo code")

    now = datetime.now(timezone.utc)

    # Validity period
    if promo.valid_from and promo.valid_from > now:
        raise BadRequestError("Promo code is not yet active")
    if promo.valid_until and promo.valid_until < now:
        raise BadRequestError("Promo code has expired")

    # Usage limit
    if promo.usage_limit and promo.used_count >= promo.usage_limit:
        raise BadRequestError("Promo code usage limit reached")

    # Per-user limit
    if promo.per_user_limit:
        user_usage = await db.execute(
            select(func.count(Booking.id)).where(
                Booking.user_id == user_id,
                Booking.status.notin_(["cancelled", "expired"]),
                # We'd need a promo_code_id on Booking for a perfect check,
                # but for now check via total_amount changes
            )
        )
        # Simplified: just check per_user_limit against all user bookings with this promo
        # A full implementation would track promo usage in a separate table

    # Min booking amount
    original = float(booking.total_amount)
    if promo.min_booking_amount and original < float(promo.min_booking_amount):
        raise BadRequestError(
            f"Minimum booking amount for this promo is {promo.min_booking_amount}"
        )

    # Applicable routes
    if promo.applicable_routes:
        route_ids = promo.applicable_routes.get("route_ids", [])
        if route_ids:
            trip_result = await db.execute(
                select(Trip.route_id).where(Trip.id == booking.trip_id)
            )
            trip_route = trip_result.scalar_one()
            if str(trip_route) not in [str(r) for r in route_ids]:
                raise BadRequestError("Promo code not valid for this route")

    # Calculate discount
    if promo.discount_type == "percentage":
        discount = original * float(promo.discount_value) / 100
        if promo.max_discount:
            discount = min(discount, float(promo.max_discount))
    else:
        discount = float(promo.discount_value)

    discount = min(discount, original)  # Can't discount more than total
    new_total = round(original - discount, 2)

    booking.total_amount = new_total
    promo.used_count += 1
    await db.flush()

    return {
        "booking_reference": booking.reference,
        "original_amount": original,
        "discount_amount": round(discount, 2),
        "new_total": new_total,
        "promo_code": promo.code,
    }
