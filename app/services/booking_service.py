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
) -> dict:
    booking = await get_booking_by_reference(db, reference, user_id)

    if booking.status != BookingStatus.CONFIRMED:
        raise BadRequestError(f"Cannot reschedule a {booking.status} booking")

    if len(new_seat_ids) != booking.passenger_count:
        raise BadRequestError(f"Must provide exactly {booking.passenger_count} seats")

    # Get current trip
    old_trip_result = await db.execute(
        select(Trip).where(Trip.id == booking.trip_id)
    )
    old_trip = old_trip_result.scalar_one()

    # Verify new trip
    new_trip_result = await db.execute(select(Trip).where(Trip.id == new_trip_id))
    new_trip = new_trip_result.scalar_one_or_none()
    if not new_trip:
        raise NotFoundError("New trip not found")
    if new_trip.id == old_trip.id:
        raise BadRequestError("New trip is the same as current trip")
    if new_trip.status not in ("scheduled",):
        raise BadRequestError("New trip is not available for booking")
    if new_trip.route_id != old_trip.route_id:
        raise BadRequestError("Can only reschedule to a trip on the same route. Cancel and rebook for a different route.")

    # Verify trip is in the future
    now = datetime.now(timezone.utc)
    new_dep = datetime.combine(new_trip.departure_date, new_trip.departure_time, tzinfo=timezone.utc)
    if new_dep <= now:
        raise BadRequestError("Cannot reschedule to a past trip")

    if new_trip.available_seats < booking.passenger_count:
        raise BadRequestError("Not enough seats on new trip")

    # Verify new seats
    new_seats_result = await db.execute(
        select(TripSeat).where(TripSeat.id.in_(new_seat_ids), TripSeat.trip_id == new_trip_id)
    )
    new_seats = {s.id: s for s in new_seats_result.scalars().all()}
    if len(new_seats) != len(new_seat_ids):
        raise BadRequestError("One or more new seats not found")
    for seat in new_seats.values():
        if seat.status != SeatStatus.AVAILABLE:
            raise BadRequestError(f"Seat {seat.seat_number} is not available")

    # Calculate fare difference
    new_total = sum(float(new_trip.price) + float(new_seats[sid].price_modifier) for sid in new_seat_ids)
    old_total = float(booking.total_amount)
    fare_diff = round(new_total - old_total, 2)

    # Release old seats
    old_seat_ids = [p.seat_id for p in booking.passengers]
    if old_seat_ids:
        await db.execute(
            update(TripSeat).where(TripSeat.id.in_(old_seat_ids))
            .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
        )
        old_trip.available_seats += len(old_seat_ids)

    # Assign new seats
    new_seat_list = list(new_seats.values())
    for i, passenger in enumerate(booking.passengers):
        new_seat = new_seat_list[i]
        passenger.seat_id = new_seat.id
        passenger.qr_code_data = f"{booking.reference}-{new_seat.seat_number}-{passenger.first_name.upper()}"
        new_seat.status = SeatStatus.BOOKED

    new_trip.available_seats -= booking.passenger_count

    # Update booking
    old_trip_id = booking.trip_id
    booking.trip_id = new_trip_id
    booking.total_amount = new_total
    booking.rescheduled_from_trip_id = old_trip_id
    booking.rescheduled_at = now

    # Handle refund to wallet if cheaper
    refund_amount = 0.0
    if fare_diff < 0:
        refund_amount = abs(fare_diff)
        from app.services.payment_service import get_or_create_wallet
        wallet = await get_or_create_wallet(db, user_id)
        wallet.balance = float(wallet.balance) + refund_amount
        from app.models.payment import WalletTransaction
        from app.core.constants import WalletTxType
        tx = WalletTransaction(
            wallet_id=wallet.id, type=WalletTxType.REFUND,
            amount=refund_amount, balance_after=float(wallet.balance),
            reference=f"reschedule-{booking.reference}",
            description=f"Fare difference refund for reschedule of {booking.reference}",
        )
        db.add(tx)

    await db.flush()

    # Push notification
    try:
        from app.services.push_notification_service import send_push_to_user
        dep_date = new_trip.departure_date.strftime("%d %b")
        dep_time = new_trip.departure_time.strftime("%H:%M")
        await send_push_to_user(
            db, user_id, "Booking Rescheduled",
            f"Your booking {booking.reference} has been rescheduled to {dep_date} at {dep_time}.",
            {"type": "booking_rescheduled", "booking_ref": booking.reference},
            app_type="customer",
        )
    except Exception:
        pass

    return {
        "reference": booking.reference,
        "status": booking.status,
        "old_trip_id": str(old_trip_id),
        "new_trip_id": str(new_trip_id),
        "old_amount": old_total,
        "new_amount": new_total,
        "fare_difference": fare_diff,
        "refund_amount": refund_amount,
        "rescheduled_at": str(booking.rescheduled_at),
    }


async def get_reschedule_options(
    db: AsyncSession, user_id: uuid.UUID, reference: str
) -> list[dict]:
    booking = await get_booking_by_reference(db, reference, user_id)
    if booking.status != BookingStatus.CONFIRMED:
        raise BadRequestError(f"Cannot reschedule a {booking.status} booking")

    # Get current trip's route
    trip_result = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
    trip = trip_result.scalar_one()

    from app.models.route import Route
    now = datetime.now(timezone.utc)
    today = now.date()

    result = await db.execute(
        select(Trip)
        .options(selectinload(Trip.route))
        .where(
            Trip.route_id == trip.route_id,
            Trip.id != booking.trip_id,
            Trip.departure_date >= today,
            Trip.status == "scheduled",
            Trip.available_seats >= booking.passenger_count,
        )
        .order_by(Trip.departure_date.asc(), Trip.departure_time.asc())
        .limit(20)
    )
    trips = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "departure_date": str(t.departure_date),
            "departure_time": str(t.departure_time),
            "price": float(t.price),
            "available_seats": t.available_seats,
            "total_seats": t.total_seats,
            "fare_difference": round(float(t.price) * booking.passenger_count - float(booking.total_amount), 2),
        }
        for t in trips
    ]


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

    from app.models.promo_usage import PromoUsage
    usage = PromoUsage(
        promo_id=promo.id,
        user_id=user_id,
        booking_id=booking.id,
        discount_applied=discount,
    )
    db.add(usage)
    await db.flush()

    return {
        "booking_reference": booking.reference,
        "original_amount": original,
        "discount_amount": round(discount, 2),
        "new_total": new_total,
        "promo_code": promo.code,
    }


async def transfer_booking(
    db: AsyncSession,
    user_id: uuid.UUID,
    reference: str,
    recipient_phone: str,
    recipient_name: str,
    recipient_email: str | None = None,
    is_admin: bool = False,
) -> dict:
    booking = await get_booking_by_reference(db, reference, user_id if not is_admin else None)

    if booking.status != BookingStatus.CONFIRMED:
        raise BadRequestError(f"Cannot transfer a {booking.status} booking")

    if booking.transferred_from_user_id:
        raise BadRequestError("This booking has already been transferred once")

    # Check departure time
    trip_result = await db.execute(
        select(Trip).options(selectinload(Trip.route)).where(Trip.id == booking.trip_id)
    )
    trip = trip_result.scalar_one()
    dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    hours_until = (dep_dt - now).total_seconds() / 3600

    if not is_admin and hours_until < 2:
        raise BadRequestError("Transfers must be made at least 2 hours before departure")

    if dep_dt <= now:
        raise BadRequestError("Cannot transfer a booking for a departed trip")

    # Find or create recipient
    from app.models.user import User
    recipient_q = await db.execute(select(User).where(User.phone == recipient_phone))
    recipient = recipient_q.scalar_one_or_none()

    if not recipient:
        from app.core.security import hash_password
        recipient = User(
            first_name=recipient_name.split(" ")[0],
            last_name=" ".join(recipient_name.split(" ")[1:]) or "",
            phone=recipient_phone,
            email=recipient_email,
            role="passenger",
            is_active=True,
            has_logged_in=False,
            created_by=user_id,
            password_hash=hash_password(uuid.uuid4().hex[:16]),
        )
        db.add(recipient)
        await db.flush()
        await db.refresh(recipient)

    if recipient.id == booking.user_id:
        raise BadRequestError("Cannot transfer to yourself")

    # Transfer
    original_user_id = booking.user_id
    booking.transferred_from_user_id = original_user_id
    booking.transferred_at = now
    booking.user_id = recipient.id
    booking.contact_phone = recipient_phone
    if recipient_email:
        booking.contact_email = recipient_email

    # Update primary passenger name
    primary = next((p for p in booking.passengers if p.is_primary), booking.passengers[0] if booking.passengers else None)
    if primary:
        names = recipient_name.split(" ", 1)
        primary.first_name = names[0]
        primary.last_name = names[1] if len(names) > 1 else ""
        primary.phone = recipient_phone
        primary.qr_code_data = f"{booking.reference}-{primary.seat.seat_number if primary.seat else '?'}-{primary.first_name.upper()}"

    await db.flush()

    # Notifications
    try:
        from app.services.push_notification_service import send_push_to_user
        await send_push_to_user(
            db, original_user_id, "Booking Transferred",
            f"Your booking {booking.reference} has been transferred to {recipient_name}.",
            {"type": "booking_transferred", "booking_ref": booking.reference},
            app_type="customer",
        )
        await send_push_to_user(
            db, recipient.id, "Booking Received",
            f"A trip booking has been transferred to you. Ref: {booking.reference}",
            {"type": "booking_received", "booking_ref": booking.reference},
            app_type="customer",
        )
    except Exception:
        pass

    route_name = trip.route.name if trip.route else "your trip"
    return {
        "reference": booking.reference,
        "transferred_to": recipient_name,
        "transferred_to_phone": recipient_phone,
        "route": route_name,
        "departure_date": str(trip.departure_date),
        "departure_time": str(trip.departure_time),
        "transferred_at": str(booking.transferred_at),
    }


async def add_luggage(
    db: AsyncSession,
    user_id: uuid.UUID,
    reference: str,
    quantity: int,
    payment_method: str = "wallet",
    is_admin: bool = False,
    payment_reference: str | None = None,
) -> dict:
    booking = await get_booking_by_reference(db, reference, user_id if not is_admin else None)

    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN):
        raise BadRequestError(f"Cannot add luggage to a {booking.status} booking")

    if quantity < 1 or quantity > 10:
        raise BadRequestError("Quantity must be between 1 and 10")

    # Get trip and route for pricing
    trip_result = await db.execute(
        select(Trip).options(selectinload(Trip.route)).where(Trip.id == booking.trip_id)
    )
    trip = trip_result.scalar_one()
    dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    if dep_dt <= datetime.now(timezone.utc):
        raise BadRequestError("Cannot add luggage to a departed trip")

    # Price per bag
    unit_price = float(trip.route.extra_luggage_price) if trip.route and trip.route.extra_luggage_price else 2000.0
    total_price = round(unit_price * quantity, 2)

    from app.models.booking_addon import BookingAddon

    addon = BookingAddon(
        booking_id=booking.id,
        addon_type="extra_luggage",
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
    )

    # Process payment
    if payment_method == "wallet" and not is_admin:
        from app.services.payment_service import get_or_create_wallet
        wallet = await get_or_create_wallet(db, user_id)
        if float(wallet.balance) < total_price:
            raise BadRequestError(f"Insufficient wallet balance. Need \u20a6{total_price:,.2f}, have \u20a6{float(wallet.balance):,.2f}")
        wallet.balance = float(wallet.balance) - total_price
        from app.models.payment import WalletTransaction, Payment
        from app.core.constants import WalletTxType, PaymentStatus
        tx = WalletTransaction(
            wallet_id=wallet.id, type=WalletTxType.PAYMENT,
            amount=total_price, balance_after=float(wallet.balance),
            reference=f"luggage-{booking.reference}-{quantity}",
            description=f"Extra luggage ({quantity} bags) for {booking.reference}",
        )
        db.add(tx)
        payment = Payment(
            booking_id=booking.id, user_id=user_id,
            amount=total_price, method="wallet",
            status=PaymentStatus.SUCCESSFUL, gateway="wallet",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        await db.flush()
        addon.payment_id = payment.id
        addon.status = "paid"
    elif payment_method in ("cash", "pos") and is_admin:
        from app.models.payment import Payment
        from app.core.constants import PaymentStatus
        payment = Payment(
            booking_id=booking.id, user_id=booking.user_id,
            amount=total_price, method=payment_method,
            status=PaymentStatus.SUCCESSFUL, gateway="terminal",
            paid_at=datetime.now(timezone.utc),
            gateway_reference=payment_reference,
        )
        db.add(payment)
        await db.flush()
        addon.payment_id = payment.id
        addon.status = "paid"
    else:
        addon.status = "pending"

    db.add(addon)
    await db.flush()

    return {
        "id": str(addon.id),
        "booking_ref": booking.reference,
        "addon_type": addon.addon_type,
        "quantity": addon.quantity,
        "unit_price": float(addon.unit_price),
        "total_price": float(addon.total_price),
        "status": addon.status,
    }


async def get_booking_addons(db: AsyncSession, reference: str, user_id: uuid.UUID | None = None) -> list[dict]:
    booking = await get_booking_by_reference(db, reference, user_id)
    from app.models.booking_addon import BookingAddon
    result = await db.execute(
        select(BookingAddon).where(BookingAddon.booking_id == booking.id).order_by(BookingAddon.created_at.desc())
    )
    return [
        {
            "id": str(a.id),
            "addon_type": a.addon_type,
            "quantity": a.quantity,
            "unit_price": float(a.unit_price),
            "total_price": float(a.total_price),
            "status": a.status,
            "created_at": str(a.created_at),
        }
        for a in result.scalars().all()
    ]
