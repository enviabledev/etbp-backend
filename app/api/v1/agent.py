import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.constants import BookingStatus, PaymentStatus, SeatStatus, UserRole
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.security import generate_booking_reference, hash_password
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking, BookingPassenger
from app.models.driver import Driver
from app.models.payment import Payment
from app.models.route import Route
from app.models.schedule import Trip, TripSeat
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services.audit_service import log_action

router = APIRouter(prefix="/agent", tags=["Agent"])


class AgentContext:
    def __init__(self, user: User, terminal_id: uuid.UUID):
        self.user = user
        self.terminal_id = terminal_id
        self.user_id = user.id


async def get_current_agent(current_user: CurrentUser, db: DBSession) -> AgentContext:
    if current_user.role != UserRole.AGENT.value:
        raise ForbiddenError("Only agents can access this resource")
    if not current_user.assigned_terminal_id:
        raise ForbiddenError("Agent has no assigned terminal")
    return AgentContext(user=current_user, terminal_id=current_user.assigned_terminal_id)


AgentDep = Depends(get_current_agent)


# ── Profile ──


@router.get("/profile")
async def get_agent_profile(db: DBSession, agent: AgentContext = AgentDep):
    from app.models.route import Terminal
    term = None
    if agent.terminal_id:
        t_result = await db.execute(select(Terminal).where(Terminal.id == agent.terminal_id))
        term = t_result.scalar_one_or_none()
    user = agent.user
    return {
        "id": str(user.id),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "terminal": {
            "id": str(term.id),
            "name": term.name,
            "city": term.city,
            "address": term.address,
        } if term else None,
    }


# ── Dashboard ──


@router.get("/dashboard")
async def get_dashboard(db: DBSession, agent: AgentContext = AgentDep):
    today = date.today()

    # Trips today from this terminal
    trips_q = await db.execute(
        select(func.count(Trip.id)).join(Route, Route.id == Trip.route_id).where(
            Route.origin_terminal_id == agent.terminal_id,
            Trip.departure_date == today,
        )
    )
    trips_today = trips_q.scalar() or 0

    # Bookings by this agent today
    bookings_q = await db.execute(
        select(
            func.count(Booking.id),
            func.coalesce(func.sum(Booking.total_amount), 0),
        ).where(
            Booking.booked_by_user_id == agent.user_id,
            func.date(Booking.created_at) == today,
        )
    )
    bk = bookings_q.one()

    # Checked in today
    checkin_q = await db.execute(
        select(func.count(Booking.id)).join(Trip, Trip.id == Booking.trip_id).join(Route, Route.id == Trip.route_id).where(
            Route.origin_terminal_id == agent.terminal_id,
            Trip.departure_date == today,
            Booking.status == BookingStatus.CHECKED_IN.value,
        )
    )
    checked_in = checkin_q.scalar() or 0

    # Next departure
    next_q = await db.execute(
        select(Trip).options(
            selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Trip.route).selectinload(Route.destination_terminal),
        ).join(Route, Route.id == Trip.route_id).where(
            Route.origin_terminal_id == agent.terminal_id,
            Trip.departure_date == today,
            Trip.status.in_(["scheduled", "boarding"]),
        ).order_by(Trip.departure_time.asc()).limit(1)
    )
    next_trip = next_q.scalar_one_or_none()

    return {
        "trips_today": trips_today,
        "bookings_today": bk[0],
        "revenue_today": float(bk[1]),
        "checked_in_today": checked_in,
        "next_departure": {
            "id": str(next_trip.id),
            "route": next_trip.route.name if next_trip.route else None,
            "departure_time": str(next_trip.departure_time),
            "status": next_trip.status,
        } if next_trip else None,
    }


# ── Trips ──


@router.get("/trips")
async def list_agent_trips(db: DBSession, agent: AgentContext = AgentDep):
    today = date.today()
    tomorrow = today + timedelta(days=1)

    result = await db.execute(
        select(Trip)
        .options(
            selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Trip.vehicle).selectinload(Vehicle.vehicle_type),
            selectinload(Trip.driver).selectinload(Driver.user),
        )
        .join(Route, Route.id == Trip.route_id)
        .where(
            Route.origin_terminal_id == agent.terminal_id,
            Trip.departure_date.in_([today, tomorrow]),
        )
        .order_by(Trip.departure_date.asc(), Trip.departure_time.asc())
    )
    trips = result.scalars().all()

    items = []
    for trip in trips:
        count_q = await db.execute(
            select(
                func.count(Booking.id).label("booked"),
                func.count(Booking.id).filter(Booking.status == "checked_in").label("checked_in"),
            ).where(
                Booking.trip_id == trip.id,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        counts = count_q.one()

        items.append({
            "id": str(trip.id),
            "departure_date": str(trip.departure_date),
            "departure_time": str(trip.departure_time),
            "status": trip.status,
            "route_name": trip.route.name if trip.route else None,
            "destination": trip.route.destination_terminal.city if trip.route and trip.route.destination_terminal else None,
            "vehicle_plate": trip.vehicle.plate_number if trip.vehicle else None,
            "vehicle_type": trip.vehicle.vehicle_type.name if trip.vehicle and trip.vehicle.vehicle_type else None,
            "driver_name": f"{trip.driver.user.first_name} {trip.driver.user.last_name}" if trip.driver and trip.driver.user else None,
            "booked": counts.booked,
            "checked_in": counts.checked_in,
            "total_seats": trip.total_seats,
        })

    return {"items": items}


@router.get("/trips/{trip_id}")
async def get_agent_trip(trip_id: uuid.UUID, db: DBSession, agent: AgentContext = AgentDep):
    result = await db.execute(
        select(Trip)
        .options(
            selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Trip.vehicle).selectinload(Vehicle.vehicle_type),
            selectinload(Trip.seats),
        )
        .where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.route and trip.route.origin_terminal_id != agent.terminal_id:
        raise ForbiddenError("This trip is not from your terminal")

    count_q = await db.execute(
        select(
            func.count(Booking.id).label("booked"),
            func.count(Booking.id).filter(Booking.status == "checked_in").label("checked_in"),
        ).where(Booking.trip_id == trip_id, Booking.status.in_(["confirmed", "checked_in"]))
    )
    counts = count_q.one()

    return {
        "id": str(trip.id),
        "departure_date": str(trip.departure_date),
        "departure_time": str(trip.departure_time),
        "status": trip.status,
        "price": float(trip.price),
        "total_seats": trip.total_seats,
        "available_seats": trip.available_seats,
        "route": {
            "name": trip.route.name,
            "origin": trip.route.origin_terminal.name if trip.route.origin_terminal else None,
            "destination": trip.route.destination_terminal.name if trip.route.destination_terminal else None,
        } if trip.route else None,
        "vehicle": {"plate_number": trip.vehicle.plate_number, "type": trip.vehicle.vehicle_type.name if trip.vehicle.vehicle_type else None} if trip.vehicle else None,
        "booked": counts.booked,
        "checked_in": counts.checked_in,
        "seats": [{"id": str(s.id), "seat_number": s.seat_number, "seat_row": s.seat_row, "seat_column": s.seat_column, "status": s.status, "price_modifier": float(s.price_modifier)} for s in sorted(trip.seats, key=lambda s: (s.seat_row or 0, s.seat_column or 0))],
    }


# ── Manifest ──


@router.get("/trips/{trip_id}/manifest")
async def get_agent_manifest(trip_id: uuid.UUID, db: DBSession, agent: AgentContext = AgentDep):
    # Verify terminal
    trip_q = await db.execute(
        select(Trip).options(selectinload(Trip.route)).where(Trip.id == trip_id)
    )
    trip = trip_q.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.route and trip.route.origin_terminal_id != agent.terminal_id:
        raise ForbiddenError("This trip is not from your terminal")

    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers).selectinload(BookingPassenger.seat), selectinload(Booking.payments))
        .where(Booking.trip_id == trip_id, Booking.status.in_(["confirmed", "checked_in"]))
    )
    bookings = result.scalars().all()

    manifest = []
    for booking in bookings:
        payment = next((p for p in (booking.payments or []) if p.status in ("successful", "completed")), None)
        for p in booking.passengers:
            manifest.append({
                "booking_id": str(booking.id),
                "booking_ref": booking.reference,
                "passenger_name": f"{p.first_name} {p.last_name}",
                "phone": p.phone,
                "seat_number": p.seat.seat_number if p.seat else None,
                "booking_status": booking.status,
                "checked_in": p.checked_in,
                "checked_in_at": str(booking.checked_in_at) if booking.checked_in_at and p.checked_in else None,
                "payment_method": payment.method if payment else None,
                "payment_status": "paid" if payment and payment.status in ("successful", "completed") else "unpaid",
                "amount_due": float(booking.total_amount) if not (payment and payment.status in ("successful", "completed")) else 0,
            })
    manifest.sort(key=lambda m: m["seat_number"] or "")
    return {"trip_id": str(trip_id), "passengers": manifest, "total": len(manifest)}


# ── Payment Helpers ──


async def _booking_is_paid(db: DBSession, booking_id: uuid.UUID) -> bool:
    """Check if a booking has a successful payment."""
    result = await db.execute(
        select(Payment).where(
            Payment.booking_id == booking_id,
            Payment.status.in_(["successful", "completed"]),
        )
    )
    return result.scalar_one_or_none() is not None


# ── Check-in ──


@router.post("/trips/{trip_id}/checkin/{booking_id}")
async def agent_checkin(trip_id: uuid.UUID, booking_id: uuid.UUID, db: DBSession, agent: AgentContext = AgentDep):
    result = await db.execute(
        select(Booking).options(selectinload(Booking.passengers).selectinload(BookingPassenger.seat))
        .where(Booking.id == booking_id, Booking.trip_id == trip_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status == "checked_in":
        raise BadRequestError("Already checked in")

    # Verify payment
    if not await _booking_is_paid(db, booking.id):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=402, content={
            "error": "payment_required",
            "amount_due": float(booking.total_amount),
            "currency": booking.currency,
            "booking_ref": booking.reference,
            "booking_id": str(booking.id),
            "message": "Payment required before check-in",
        })

    booking.status = BookingStatus.CHECKED_IN.value
    booking.checked_in_at = datetime.now(timezone.utc)
    for p in booking.passengers:
        p.checked_in = True
    await db.flush()

    primary = next((p for p in booking.passengers if p.is_primary), booking.passengers[0] if booking.passengers else None)
    return {
        "booking_ref": booking.reference,
        "passenger_name": f"{primary.first_name} {primary.last_name}" if primary else "Unknown",
        "seat_number": primary.seat.seat_number if primary and primary.seat else None,
        "checked_in_at": str(booking.checked_in_at),
    }


# ── Booking Payment ──


class BookingPayRequest(BaseModel):
    payment_method: str  # cash, pos, transfer
    payment_reference: str | None = None


@router.post("/bookings/{booking_ref}/pay")
async def pay_booking(booking_ref: str, data: BookingPayRequest, db: DBSession, agent: AgentContext = AgentDep):
    """Record payment for a pay-at-terminal booking."""
    result = await db.execute(select(Booking).where(Booking.reference == booking_ref.upper()))
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if await _booking_is_paid(db, booking.id):
        raise BadRequestError("Booking is already paid")

    payment = Payment(
        booking_id=booking.id, user_id=booking.user_id,
        amount=float(booking.total_amount), method=data.payment_method,
        status=PaymentStatus.SUCCESSFUL.value, gateway="terminal",
        paid_at=datetime.now(timezone.utc), gateway_reference=data.payment_reference,
    )
    db.add(payment)
    booking.status = BookingStatus.CONFIRMED.value
    booking.payment_deadline = None
    await db.flush()

    await log_action(db, agent.user_id, "agent_collect_payment", "booking", str(booking.id), {
        "reference": booking_ref, "amount": float(booking.total_amount), "method": data.payment_method,
    })

    return {
        "booking_ref": booking.reference, "status": booking.status,
        "amount_paid": float(booking.total_amount), "payment_method": data.payment_method,
        "message": "Payment recorded. Passenger can now be checked in.",
    }


# ── Customer Search + Create ──


@router.get("/customers/search")
async def search_customers(q: str = Query(..., min_length=2), *, db: DBSession, agent: AgentContext = AgentDep):
    cleaned = q.strip()
    result = await db.execute(
        select(User).where(
            or_(
                User.phone.ilike(f"%{cleaned}%"),
                User.email.ilike(f"%{cleaned}%"),
                User.first_name.ilike(f"%{cleaned}%"),
                User.last_name.ilike(f"%{cleaned}%"),
            ),
            User.role == "passenger",
        ).limit(10)
    )
    users = result.scalars().all()
    return {"items": [{"id": str(u.id), "first_name": u.first_name, "last_name": u.last_name, "email": u.email, "phone": u.phone, "has_logged_in": getattr(u, "has_logged_in", False)} for u in users]}


class CreateCustomerRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=20)
    email: EmailStr | None = None


@router.post("/customers", status_code=201)
async def create_customer(data: CreateCustomerRequest, db: DBSession, agent: AgentContext = AgentDep):
    existing = await db.execute(select(User).where(User.phone == data.phone))
    if existing.scalar_one_or_none():
        raise BadRequestError("A customer with this phone already exists")
    user = User(
        first_name=data.first_name, last_name=data.last_name,
        phone=data.phone, email=data.email,
        role="passenger", is_active=True, has_logged_in=False,
        created_by=agent.user_id,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return {"id": str(user.id), "first_name": user.first_name, "last_name": user.last_name, "phone": user.phone, "email": user.email}


# ── Booking ──


class SeatPassenger(BaseModel):
    seat_id: uuid.UUID
    passenger_name: str
    passenger_phone: str | None = None
    passenger_gender: str | None = None


class AgentBookingRequest(BaseModel):
    customer_id: uuid.UUID
    trip_id: uuid.UUID
    seats: list[SeatPassenger] = Field(..., min_length=1)
    payment_method: str = "cash"
    payment_reference: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


@router.post("/bookings", status_code=201)
async def create_agent_booking(data: AgentBookingRequest, db: DBSession, agent: AgentContext = AgentDep):
    # Validate customer
    cust_q = await db.execute(select(User).where(User.id == data.customer_id))
    customer = cust_q.scalar_one_or_none()
    if not customer:
        raise NotFoundError("Customer not found")

    # Validate trip
    trip_q = await db.execute(select(Trip).where(Trip.id == data.trip_id))
    trip = trip_q.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("Trip is not available for booking")

    # Validate seats
    seat_ids = [s.seat_id for s in data.seats]
    seats_q = await db.execute(select(TripSeat).where(TripSeat.id.in_(seat_ids), TripSeat.trip_id == data.trip_id))
    seats = {s.id: s for s in seats_q.scalars().all()}
    if len(seats) != len(seat_ids):
        raise BadRequestError("One or more seats not found")
    for seat in seats.values():
        if seat.status == SeatStatus.BOOKED:
            raise BadRequestError(f"Seat {seat.seat_number} is already booked")

    # Create booking
    reference = generate_booking_reference()
    total = sum(float(trip.price) + float(seats[s.seat_id].price_modifier) for s in data.seats)

    booking = Booking(
        reference=reference, user_id=customer.id, trip_id=data.trip_id,
        booked_by_user_id=agent.user_id, total_amount=total,
        passenger_count=len(data.seats), contact_phone=customer.phone,
        contact_email=customer.email, status=BookingStatus.CONFIRMED.value,
        emergency_contact_name=data.emergency_contact_name,
        emergency_contact_phone=data.emergency_contact_phone,
    )
    db.add(booking)
    await db.flush()

    # Create passengers + mark seats
    for i, s in enumerate(data.seats):
        names = s.passenger_name.split(" ", 1)
        first = names[0]
        last = names[1] if len(names) > 1 else ""
        seat = seats[s.seat_id]
        qr = f"{reference}-{seat.seat_number}-{first.upper()}"
        passenger = BookingPassenger(
            booking_id=booking.id, seat_id=s.seat_id,
            first_name=first, last_name=last,
            gender=s.passenger_gender, phone=s.passenger_phone,
            is_primary=(i == 0), qr_code_data=qr,
        )
        db.add(passenger)
        seat.status = SeatStatus.BOOKED
        seat.locked_by_user_id = None
        seat.locked_until = None

    trip.available_seats -= len(data.seats)

    # Payment record
    payment = Payment(
        booking_id=booking.id, user_id=customer.id,
        amount=total, method=data.payment_method,
        status=PaymentStatus.SUCCESSFUL.value,
        gateway="terminal", paid_at=datetime.now(timezone.utc),
        gateway_reference=data.payment_reference,
    )
    db.add(payment)
    await db.flush()

    await log_action(db, agent.user_id, "agent_booking", "booking", str(booking.id), {
        "customer_id": str(customer.id), "reference": reference, "amount": total, "method": data.payment_method,
    })

    return {
        "id": str(booking.id), "reference": reference, "status": booking.status,
        "total_amount": total, "passenger_count": len(data.seats),
        "customer": {"name": f"{customer.first_name} {customer.last_name}", "phone": customer.phone},
    }


# ── Smart Scan Lookup ──


@router.get("/bookings/scan/{booking_ref}")
async def scan_booking_lookup(booking_ref: str, db: DBSession, agent: AgentContext = AgentDep):
    """Smart booking lookup for QR scan — returns booking + contextual actions."""
    from app.models.route import Terminal

    result = await db.execute(
        select(Booking).options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.payments),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Booking.user),
        ).where(Booking.reference == booking_ref.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")

    trip = booking.trip
    route = trip.route if trip else None
    payment = next((p for p in (booking.payments or []) if p.status in ("successful", "completed")), None)
    is_paid = payment is not None
    now = datetime.now(timezone.utc)

    # Determine if trip is from agent's terminal
    is_from_agent_terminal = route and route.origin_terminal_id == agent.terminal_id

    # Build departure datetime for comparison
    departure_dt = None
    if trip:
        departure_dt = datetime.combine(
            trip.departure_date, trip.departure_time, tzinfo=timezone.utc
        )

    # Check-in window: 24h before departure
    checkin_opens_at = departure_dt - timedelta(hours=24) if departure_dt else None
    within_checkin_window = checkin_opens_at and now >= checkin_opens_at

    # All passengers already checked in?
    all_checked_in = all(p.checked_in for p in booking.passengers) if booking.passengers else False

    # Determine payment status label
    if is_paid:
        payment_status = "paid"
    elif booking.payment_method_hint == "pay_at_terminal":
        payment_status = "pay_at_terminal"
    else:
        payment_status = "pending"

    # Determine check-in eligibility and blocked reason
    can_check_in = False
    check_in_blocked_reason = None

    if booking.status in ("expired",):
        check_in_blocked_reason = "booking_expired"
    elif booking.status in ("cancelled",):
        check_in_blocked_reason = "booking_cancelled"
    elif not is_from_agent_terminal:
        check_in_blocked_reason = "wrong_terminal"
    elif trip and trip.status in ("completed",):
        check_in_blocked_reason = "trip_completed"
    elif trip and trip.status in ("departed", "en_route"):
        check_in_blocked_reason = "trip_departed"
    elif all_checked_in:
        check_in_blocked_reason = "already_checked_in"
    elif not is_paid:
        check_in_blocked_reason = "payment_required"
    elif not within_checkin_window:
        check_in_blocked_reason = "trip_not_today"
    else:
        can_check_in = True

    # Can collect payment?
    can_collect_payment = (
        not is_paid
        and booking.status not in ("expired", "cancelled", "completed", "checked_in")
        and is_from_agent_terminal
    )

    # Get terminal name for wrong_terminal scenario
    origin_terminal_name = None
    origin_terminal_address = None
    if check_in_blocked_reason == "wrong_terminal" and route and route.origin_terminal:
        origin_terminal_name = route.origin_terminal.name
        origin_terminal_address = route.origin_terminal.address

    # Agent terminal name
    agent_terminal_q = await db.execute(select(Terminal.name).where(Terminal.id == agent.terminal_id))
    agent_terminal_name = agent_terminal_q.scalar()

    return {
        "booking": {
            "id": str(booking.id),
            "reference": booking.reference,
            "status": booking.status,
            "total_amount": float(booking.total_amount),
            "currency": booking.currency,
            "payment_status": payment_status,
            "payment_method": payment.method if payment else (booking.payment_method_hint or None),
            "payment_deadline": str(booking.payment_deadline) if booking.payment_deadline else None,
            "created_at": str(booking.created_at),
        },
        "trip": {
            "id": str(trip.id),
            "route_name": route.name if route else None,
            "origin_terminal": route.origin_terminal.name if route and route.origin_terminal else None,
            "destination_terminal": route.destination_terminal.name if route and route.destination_terminal else None,
            "departure_date": str(trip.departure_date),
            "departure_time": str(trip.departure_time),
            "status": trip.status,
            "is_from_agent_terminal": is_from_agent_terminal,
        } if trip else None,
        "passengers": [
            {
                "id": str(p.id),
                "name": f"{p.first_name} {p.last_name}",
                "seat_number": p.seat.seat_number if p.seat else None,
                "checked_in": p.checked_in,
                "checked_in_at": str(booking.checked_in_at) if booking.checked_in_at and p.checked_in else None,
            }
            for p in booking.passengers
        ],
        "customer": {
            "name": f"{booking.user.first_name} {booking.user.last_name}" if booking.user else None,
            "phone": booking.user.phone if booking.user else booking.contact_phone,
            "email": booking.user.email if booking.user else booking.contact_email,
        },
        "actions": {
            "can_check_in": can_check_in,
            "check_in_blocked_reason": check_in_blocked_reason,
            "can_collect_payment": can_collect_payment,
            "can_refund": False,
            "amount_due": float(booking.total_amount) if not is_paid else 0,
            "check_in_available_from": str(checkin_opens_at) if check_in_blocked_reason == "trip_not_today" and checkin_opens_at else None,
            "wrong_terminal_name": origin_terminal_name,
            "wrong_terminal_address": origin_terminal_address,
            "agent_terminal_name": agent_terminal_name,
        },
    }


# ── Booking Lookup ──


@router.get("/bookings/{booking_ref}")
async def lookup_booking(booking_ref: str, db: DBSession, agent: AgentContext = AgentDep):
    result = await db.execute(
        select(Booking).options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
            selectinload(Booking.payments),
            selectinload(Booking.trip).selectinload(Trip.route),
        ).where(Booking.reference == booking_ref.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")

    payment = next((p for p in (booking.payments or []) if p.status in ("successful", "completed")), None)
    return {
        "id": str(booking.id), "reference": booking.reference, "status": booking.status,
        "total_amount": float(booking.total_amount), "currency": booking.currency,
        "passenger_count": booking.passenger_count,
        "contact_phone": booking.contact_phone, "contact_email": booking.contact_email,
        "created_at": str(booking.created_at),
        "route_name": booking.trip.route.name if booking.trip and booking.trip.route else None,
        "departure_date": str(booking.trip.departure_date) if booking.trip else None,
        "departure_time": str(booking.trip.departure_time) if booking.trip else None,
        "passengers": [{"name": f"{p.first_name} {p.last_name}", "seat": p.seat.seat_number if p.seat else None, "checked_in": p.checked_in} for p in booking.passengers],
        "payment": {"method": payment.method, "status": payment.status, "reference": payment.gateway_reference} if payment else None,
    }


@router.post("/bookings/{booking_ref}/cancel")
async def cancel_agent_booking(booking_ref: str, db: DBSession, agent: AgentContext = AgentDep):
    result = await db.execute(
        select(Booking).options(selectinload(Booking.passengers))
        .where(Booking.reference == booking_ref.upper())
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status in ("cancelled", "expired", "completed"):
        raise BadRequestError(f"Cannot cancel a {booking.status} booking")

    booking.status = BookingStatus.CANCELLED.value
    booking.cancellation_reason = "Cancelled by terminal agent"
    booking.cancelled_at = datetime.now(timezone.utc)

    # Release seats
    from sqlalchemy import update
    seat_ids = [p.seat_id for p in booking.passengers]
    if seat_ids:
        await db.execute(update(TripSeat).where(TripSeat.id.in_(seat_ids)).values(status=SeatStatus.AVAILABLE.value, locked_by_user_id=None, locked_until=None))
        trip_q = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
        trip = trip_q.scalar_one()
        trip.available_seats += len(seat_ids)

    await db.flush()
    await log_action(db, agent.user_id, "agent_cancel_booking", "booking", str(booking.id), {"reference": booking_ref})
    return {"reference": booking.reference, "status": booking.status}


# ── History ──


@router.get("/history")
async def agent_history(db: DBSession, agent: AgentContext = AgentDep, date_filter: date | None = None):
    target = date_filter or date.today()
    result = await db.execute(
        select(Booking).options(selectinload(Booking.trip).selectinload(Trip.route))
        .where(Booking.booked_by_user_id == agent.user_id, func.date(Booking.created_at) == target)
        .order_by(Booking.created_at.desc())
    )
    bookings = result.scalars().all()
    total_revenue = sum(float(b.total_amount) for b in bookings if b.status not in ("cancelled", "expired"))
    return {
        "items": [{
            "id": str(b.id), "reference": b.reference, "status": b.status,
            "total_amount": float(b.total_amount), "passenger_count": b.passenger_count,
            "route_name": b.trip.route.name if b.trip and b.trip.route else None,
            "created_at": str(b.created_at),
        } for b in bookings],
        "total_revenue": total_revenue,
        "count": len(bookings),
    }


# ── Shift Report ──


@router.get("/shift-report")
async def shift_report(db: DBSession, agent: AgentContext = AgentDep, report_date: date | None = None):
    target = report_date or date.today()

    # All bookings by this agent on the target date
    result = await db.execute(
        select(Booking).options(
            selectinload(Booking.trip).selectinload(Trip.route),
            selectinload(Booking.payments),
        ).where(
            Booking.booked_by_user_id == agent.user_id,
            func.date(Booking.created_at) == target,
        ).order_by(Booking.created_at.asc())
    )
    bookings = result.scalars().all()

    revenue = {"cash": 0.0, "pos": 0.0, "transfer": 0.0, "other": 0.0, "total": 0.0}
    total_passengers = 0
    booking_items = []
    cancellations = []

    for b in bookings:
        if b.status == "cancelled":
            cancellations.append({
                "reference": b.reference, "route_name": b.trip.route.name if b.trip and b.trip.route else None,
                "amount": float(b.total_amount), "cancelled_at": str(b.cancelled_at) if b.cancelled_at else None,
            })
            continue

        amount = float(b.total_amount)
        payment = next((p for p in (b.payments or []) if p.status in ("successful", "completed")), None)
        method = payment.method if payment else "cash"
        if method in revenue:
            revenue[method] += amount
        else:
            revenue["other"] += amount
        revenue["total"] += amount
        total_passengers += b.passenger_count

        booking_items.append({
            "reference": b.reference, "route_name": b.trip.route.name if b.trip and b.trip.route else None,
            "passengers": b.passenger_count, "amount": amount,
            "payment_method": method, "created_at": str(b.created_at),
        })

    # Check-ins by this agent (approximate — bookings checked in that were booked by this agent)
    checkin_q = await db.execute(
        select(func.count(Booking.id)).where(
            Booking.booked_by_user_id == agent.user_id,
            Booking.status == "checked_in",
            func.date(Booking.created_at) == target,
        )
    )
    total_checkins = checkin_q.scalar() or 0

    start_time = str(bookings[0].created_at) if bookings else None
    end_time = str(bookings[-1].created_at) if bookings else None

    return {
        "date": str(target),
        "agent_name": f"{agent.user.first_name} {agent.user.last_name}",
        "total_bookings": len(booking_items),
        "total_passengers": total_passengers,
        "total_checkins": total_checkins,
        "revenue": revenue,
        "bookings": booking_items,
        "cancellations": cancellations,
        "start_time": start_time,
        "end_time": end_time,
    }


# ── Seat Change ──


class ChangeSeatRequest(BaseModel):
    booking_passenger_id: uuid.UUID
    new_seat_id: uuid.UUID


@router.put("/bookings/{booking_ref}/change-seat")
async def change_seat(booking_ref: str, data: ChangeSeatRequest, db: DBSession, agent: AgentContext = AgentDep):
    # Find passenger
    result = await db.execute(
        select(BookingPassenger).options(selectinload(BookingPassenger.seat))
        .where(BookingPassenger.id == data.booking_passenger_id)
    )
    passenger = result.scalar_one_or_none()
    if not passenger:
        raise NotFoundError("Passenger not found")

    # Find booking
    booking_q = await db.execute(select(Booking).where(Booking.id == passenger.booking_id))
    booking = booking_q.scalar_one_or_none()
    if not booking or booking.reference.upper() != booking_ref.upper():
        raise BadRequestError("Passenger does not belong to this booking")

    # Find new seat
    new_seat_q = await db.execute(select(TripSeat).where(TripSeat.id == data.new_seat_id, TripSeat.trip_id == booking.trip_id))
    new_seat = new_seat_q.scalar_one_or_none()
    if not new_seat:
        raise NotFoundError("Seat not found on this trip")
    if new_seat.status != SeatStatus.AVAILABLE.value:
        raise BadRequestError(f"Seat {new_seat.seat_number} is not available")

    # Release old seat
    old_seat = passenger.seat
    if old_seat:
        old_seat.status = SeatStatus.AVAILABLE.value
        old_seat.locked_by_user_id = None

    # Assign new seat
    new_seat.status = SeatStatus.BOOKED.value
    passenger.seat_id = data.new_seat_id

    # Update QR code
    passenger.qr_code_data = f"{booking.reference}-{new_seat.seat_number}-{passenger.first_name.upper()}"

    await db.flush()
    await log_action(db, agent.user_id, "agent_change_seat", "booking", str(booking.id), {
        "passenger": f"{passenger.first_name} {passenger.last_name}",
        "old_seat": old_seat.seat_number if old_seat else None,
        "new_seat": new_seat.seat_number,
    })

    return {
        "booking_ref": booking.reference,
        "passenger": f"{passenger.first_name} {passenger.last_name}",
        "old_seat": old_seat.seat_number if old_seat else None,
        "new_seat": new_seat.seat_number,
    }


# ── Agent Token ──


@router.post("/generate-token")
async def generate_token(db: DBSession, agent: AgentContext = AgentDep):
    from app.services.agent_token_service import generate_agent_token
    result = await generate_agent_token(str(agent.user_id))
    await log_action(db, agent.user_id, "agent_token_generated", "agent", str(agent.user_id))
    return result


class VerifyTokenRequest(BaseModel):
    agent_id: uuid.UUID
    code: str


@router.post("/verify-token")
async def verify_token(data: VerifyTokenRequest, db: DBSession):
    from app.services.agent_token_service import verify_agent_token
    valid = await verify_agent_token(str(data.agent_id), data.code)
    return {"valid": valid}


# ── Wallet QR Payment ──


class WalletPaymentRequest(BaseModel):
    token: str
    amount: float
    description: str | None = None
    booking_id: uuid.UUID | None = None


@router.post("/wallet-payment")
async def process_wallet_qr_payment(data: WalletPaymentRequest, db: DBSession, agent: AgentContext = AgentDep):
    from app.services.wallet_qr_service import process_wallet_payment
    result = await process_wallet_payment(
        db, agent.user_id, data.token, data.amount, data.description, data.booking_id
    )

    # If a booking was specified, create Payment record and confirm the booking
    if data.booking_id:
        booking_q = await db.execute(select(Booking).where(Booking.id == data.booking_id))
        booking = booking_q.scalar_one_or_none()
        if booking and booking.status in (BookingStatus.PENDING.value, "pending"):
            # Create Payment record
            payment = Payment(
                booking_id=booking.id,
                user_id=booking.user_id,
                amount=data.amount,
                method="wallet",
                status=PaymentStatus.SUCCESSFUL.value,
                gateway="wallet_qr",
                paid_at=datetime.now(timezone.utc),
                gateway_reference=result.get("transaction_id"),
            )
            db.add(payment)

            # Confirm booking and clear deadline
            booking.status = BookingStatus.CONFIRMED.value
            booking.payment_method_hint = "wallet"
            booking.payment_deadline = None
            await db.flush()

    await log_action(db, agent.user_id, "agent_wallet_payment", "wallet", result.get("transaction_id"), {
        "amount": data.amount, "customer": result.get("customer_name"),
        "booking_id": str(data.booking_id) if data.booking_id else None,
    })
    return result


# ── Booking OTP Flow ──


class SendCustomerOTPRequest(BaseModel):
    customer_id: uuid.UUID


@router.post("/bookings/send-customer-otp")
async def send_customer_otp(data: SendCustomerOTPRequest, db: DBSession, agent: AgentContext = AgentDep):
    from app.models.user import User
    user_q = await db.execute(select(User).where(User.id == data.customer_id))
    customer = user_q.scalar_one_or_none()
    if not customer:
        raise NotFoundError("Customer not found")
    if not customer.phone:
        raise BadRequestError("Customer has no phone number")

    # Send OTP
    try:
        from app.integrations.termii import TermiiClient
        import redis.asyncio as _redis

        if settings.termii_api_key and settings.app_env != "development":
            client = TermiiClient()
            result = await client.send_otp(customer.phone)
            pin_id = result.get("pinId")
            if pin_id:
                r = _redis.from_url(settings.redis_url)
                await r.setex(f"booking_otp:{data.customer_id}", 600, pin_id)
                await r.aclose()
        else:
            # Dev mode — accept any 6-digit code
            r = _redis.from_url(settings.redis_url)
            await r.setex(f"booking_otp:{data.customer_id}", 600, "dev_mode")
            await r.aclose()
    except Exception:
        pass

    phone = customer.phone
    masked = phone[:4] + "****" + phone[-4:] if len(phone) > 8 else phone

    return {"sent": True, "phone_masked": masked}


class VerifyCustomerOTPRequest(BaseModel):
    customer_id: uuid.UUID
    otp: str


@router.post("/bookings/verify-customer-otp")
async def verify_customer_otp(data: VerifyCustomerOTPRequest, db: DBSession, agent: AgentContext = AgentDep):
    import redis.asyncio as _redis
    r = _redis.from_url(settings.redis_url)
    pin_id = await r.get(f"booking_otp:{data.customer_id}")

    if not pin_id:
        await r.aclose()
        raise BadRequestError("OTP expired or not sent. Please resend.")

    pin_id_str = pin_id.decode() if isinstance(pin_id, bytes) else pin_id

    if pin_id_str == "dev_mode":
        # Dev mode — accept any 6-digit code
        verified = len(data.otp) == 6
    else:
        try:
            from app.integrations.termii import TermiiClient
            client = TermiiClient()
            result = await client.verify_otp(pin_id_str, data.otp)
            verified = result.get("verified") is True or result.get("status") == "success"
        except Exception:
            verified = False

    if verified:
        # Get customer phone and mark as verified
        from app.models.user import User
        user_q = await db.execute(select(User).where(User.id == data.customer_id))
        customer = user_q.scalar_one()
        if customer and customer.phone:
            await r.setex(f"phone_verified:{customer.phone}", 86400, "verified")  # 24h
        await r.delete(f"booking_otp:{data.customer_id}")
        await r.aclose()
        return {"verified": True}
    else:
        await r.aclose()
        raise BadRequestError("Invalid OTP. Please try again.")
