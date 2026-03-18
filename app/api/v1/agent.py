import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, PaymentStatus, SeatStatus, UserRole
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.security import generate_booking_reference, hash_password
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking, BookingPassenger
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
            selectinload(Trip.driver),
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
            "driver_name": f"{trip.driver.user.first_name} {trip.driver.user.last_name}" if trip.driver and hasattr(trip.driver, 'user') and trip.driver.user else None,
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
                "payment_status": payment.status if payment else "pending",
            })
    manifest.sort(key=lambda m: m["seat_number"] or "")
    return {"trip_id": str(trip_id), "passengers": manifest, "total": len(manifest)}


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
