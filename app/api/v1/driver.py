import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, UserRole
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking, BookingPassenger
from app.models.driver import Driver
from app.models.route import Route
from app.models.vehicle import Vehicle
from app.models.schedule import Trip, TripIncident, TripSeat
from app.models.user import User

router = APIRouter(prefix="/driver", tags=["Driver"])


async def get_current_driver(current_user: CurrentUser, db: DBSession) -> Driver:
    """Dependency: verify user is a driver and return their Driver record."""
    if current_user.role != UserRole.DRIVER.value:
        raise ForbiddenError("Only drivers can access this resource")
    result = await db.execute(
        select(Driver)
        .options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
        .where(Driver.user_id == current_user.id)
    )
    driver = result.scalar_one_or_none()
    if not driver:
        raise ForbiddenError("Driver profile not found")
    return driver


DriverDep = Depends(get_current_driver)


# ── Profile ──


@router.get("/profile")
async def get_driver_profile(driver: Driver = DriverDep):
    user = driver.user
    return {
        "id": str(driver.id),
        "user_id": str(user.id),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "license_number": driver.license_number,
        "license_expiry": str(driver.license_expiry),
        "license_class": driver.license_class,
        "years_experience": driver.years_experience,
        "medical_check_expiry": str(driver.medical_check_expiry) if driver.medical_check_expiry else None,
        "rating_avg": float(driver.rating_avg),
        "total_trips": driver.total_trips,
        "is_available": driver.is_available,
        "assigned_terminal": {
            "id": str(driver.assigned_terminal.id),
            "name": driver.assigned_terminal.name,
            "city": driver.assigned_terminal.city,
        } if driver.assigned_terminal else None,
    }


# ── Trips ──


@router.get("/trips")
async def list_driver_trips(
    db: DBSession,
    driver: Driver = DriverDep,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = (
        select(Trip)
        .options(
            selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Trip.vehicle).selectinload(Vehicle.vehicle_type),
        )
        .where(Trip.driver_id == driver.id)
    )
    if status:
        query = query.where(Trip.status == status)
    if date_from:
        query = query.where(Trip.departure_date >= date_from)
    if date_to:
        query = query.where(Trip.departure_date <= date_to)
    # Default: show today and future if no date filters and not filtering by status
    if not date_from and not date_to and not status:
        query = query.where(Trip.departure_date >= date.today())

    query = query.order_by(Trip.departure_date.asc(), Trip.departure_time.asc())
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    trips = result.scalars().all()

    items = []
    for trip in trips:
        # Count confirmed bookings
        count_q = await db.execute(
            select(func.count(Booking.id)).where(
                Booking.trip_id == trip.id,
                Booking.status.in_(["confirmed", "checked_in"]),
            )
        )
        passenger_count = count_q.scalar() or 0

        items.append({
            "id": str(trip.id),
            "departure_date": str(trip.departure_date),
            "departure_time": str(trip.departure_time),
            "status": trip.status,
            "route": {
                "name": trip.route.name if trip.route else None,
                "origin": trip.route.origin_terminal.city if trip.route and trip.route.origin_terminal else None,
                "destination": trip.route.destination_terminal.city if trip.route and trip.route.destination_terminal else None,
            },
            "vehicle": {
                "plate_number": trip.vehicle.plate_number,
                "type": trip.vehicle.vehicle_type.name if trip.vehicle.vehicle_type else None,
            } if trip.vehicle else None,
            "passenger_count": passenger_count,
            "total_seats": trip.total_seats,
        })

    return {"items": items, "count": len(items)}


@router.get("/trips/{trip_id}")
async def get_driver_trip_detail(trip_id: uuid.UUID, db: DBSession, driver: Driver = DriverDep):
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
    if trip.driver_id != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    # Count bookings
    booking_q = await db.execute(
        select(
            func.count(Booking.id).label("total"),
            func.count(Booking.id).filter(Booking.status == "checked_in").label("checked_in"),
        ).where(
            Booking.trip_id == trip_id,
            Booking.status.in_(["confirmed", "checked_in"]),
        )
    )
    counts = booking_q.one()

    return {
        "id": str(trip.id),
        "departure_date": str(trip.departure_date),
        "departure_time": str(trip.departure_time),
        "status": trip.status,
        "actual_departure_at": str(trip.actual_departure_at) if trip.actual_departure_at else None,
        "actual_arrival_at": str(trip.actual_arrival_at) if trip.actual_arrival_at else None,
        "notes": trip.notes,
        "price": float(trip.price),
        "total_seats": trip.total_seats,
        "available_seats": trip.available_seats,
        "route": {
            "name": trip.route.name,
            "origin_terminal": {
                "name": trip.route.origin_terminal.name,
                "city": trip.route.origin_terminal.city,
                "latitude": float(trip.route.origin_terminal.latitude) if trip.route.origin_terminal.latitude else None,
                "longitude": float(trip.route.origin_terminal.longitude) if trip.route.origin_terminal.longitude else None,
            },
            "destination_terminal": {
                "name": trip.route.destination_terminal.name,
                "city": trip.route.destination_terminal.city,
                "latitude": float(trip.route.destination_terminal.latitude) if trip.route.destination_terminal.latitude else None,
                "longitude": float(trip.route.destination_terminal.longitude) if trip.route.destination_terminal.longitude else None,
            },
        } if trip.route else None,
        "vehicle": {
            "plate_number": trip.vehicle.plate_number,
        } if trip.vehicle else None,
        "passengers_booked": counts.total,
        "passengers_checked_in": counts.checked_in,
        "inspection_data": trip.inspection_data,
    }


# ── Manifest ──


@router.get("/trips/{trip_id}/manifest")
async def get_trip_manifest(trip_id: uuid.UUID, db: DBSession, driver: Driver = DriverDep):
    # Verify assignment
    trip_q = await db.execute(select(Trip.driver_id).where(Trip.id == trip_id))
    trip_driver = trip_q.scalar_one_or_none()
    if trip_driver is None:
        raise NotFoundError("Trip not found")
    if trip_driver != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    result = await db.execute(
        select(Booking)
        .options(
            selectinload(Booking.passengers).selectinload(BookingPassenger.seat),
        )
        .where(
            Booking.trip_id == trip_id,
            Booking.status.in_(["confirmed", "checked_in"]),
        )
    )
    bookings = result.scalars().all()

    manifest = []
    for booking in bookings:
        for p in booking.passengers:
            manifest.append({
                "booking_id": str(booking.id),
                "booking_ref": booking.reference,
                "passenger_name": f"{p.first_name} {p.last_name}",
                "phone": p.phone,
                "seat_number": p.seat.seat_number if p.seat else None,
                "is_primary": p.is_primary,
                "checked_in": p.checked_in,
                "checked_in_at": str(booking.checked_in_at) if booking.checked_in_at and p.checked_in else None,
            })

    manifest.sort(key=lambda m: m["seat_number"] or "")
    return {"trip_id": str(trip_id), "passengers": manifest, "total": len(manifest)}


# ── Status Update ──


VALID_TRANSITIONS = {
    "scheduled": ["boarding", "departed"],
    "boarding": ["departed"],
    "departed": ["en_route"],
    "en_route": ["arrived"],
    "arrived": ["completed"],
}


class UpdateStatusRequest(BaseModel):
    status: str
    notes: str | None = None


@router.patch("/trips/{trip_id}/status")
async def update_trip_status(
    trip_id: uuid.UUID, data: UpdateStatusRequest, db: DBSession, driver: Driver = DriverDep
):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.driver_id != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    allowed = VALID_TRANSITIONS.get(trip.status, [])
    if data.status not in allowed:
        raise BadRequestError(f"Cannot transition from '{trip.status}' to '{data.status}'. Allowed: {allowed}")

    trip.status = data.status
    if data.notes:
        trip.notes = (trip.notes or "") + f"\n[{data.status}] {data.notes}"

    if data.status == "departed":
        trip.actual_departure_at = datetime.now(timezone.utc)
    elif data.status in ("arrived", "completed"):
        trip.actual_arrival_at = datetime.now(timezone.utc)

    await db.flush()
    return {"id": str(trip.id), "status": trip.status}


# ── Check-in ──


@router.post("/trips/{trip_id}/checkin/{booking_id}")
async def checkin_passenger(
    trip_id: uuid.UUID, booking_id: uuid.UUID, db: DBSession, driver: Driver = DriverDep
):
    # Verify trip assignment
    trip_q = await db.execute(select(Trip.driver_id).where(Trip.id == trip_id))
    trip_driver = trip_q.scalar_one_or_none()
    if trip_driver is None:
        raise NotFoundError("Trip not found")
    if trip_driver != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.passengers).selectinload(BookingPassenger.seat))
        .where(Booking.id == booking_id, Booking.trip_id == trip_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found on this trip")
    if booking.status == "checked_in":
        raise BadRequestError("Booking already checked in")

    # Verify payment
    from app.models.payment import Payment as PaymentModel
    pay_q = await db.execute(
        select(PaymentModel).where(PaymentModel.booking_id == booking.id, PaymentModel.status.in_(["successful", "completed"]))
    )
    if not pay_q.scalar_one_or_none():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=402, content={
            "error": "payment_required",
            "amount_due": float(booking.total_amount),
            "booking_ref": booking.reference,
            "message": "Payment required. Direct passenger to the terminal agent.",
        })

    booking.status = BookingStatus.CHECKED_IN.value
    booking.checked_in_at = datetime.now(timezone.utc)
    for p in booking.passengers:
        p.checked_in = True
    await db.flush()

    primary = next((p for p in booking.passengers if p.is_primary), booking.passengers[0] if booking.passengers else None)
    return {
        "booking_id": str(booking.id),
        "booking_ref": booking.reference,
        "passenger_name": f"{primary.first_name} {primary.last_name}" if primary else "Unknown",
        "seat_number": primary.seat.seat_number if primary and primary.seat else None,
        "status": booking.status,
        "checked_in_at": str(booking.checked_in_at),
        "message": "Checked in successfully",
    }


# ── Incidents ──


class ReportIncidentRequest(BaseModel):
    type: str  # breakdown, accident, passenger_issue, road_blockage, delay, other
    description: str | None = None
    severity: str = "low"  # low, medium, high


@router.post("/trips/{trip_id}/incidents", status_code=201)
async def report_incident(
    trip_id: uuid.UUID, data: ReportIncidentRequest, db: DBSession, driver: Driver = DriverDep
):
    trip_q = await db.execute(select(Trip.driver_id).where(Trip.id == trip_id))
    trip_driver = trip_q.scalar_one_or_none()
    if trip_driver is None:
        raise NotFoundError("Trip not found")
    if trip_driver != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    incident = TripIncident(
        trip_id=trip_id,
        driver_id=driver.id,
        type=data.type,
        description=data.description,
        severity=data.severity,
    )
    db.add(incident)
    await db.flush()

    return {
        "id": str(incident.id),
        "type": incident.type,
        "severity": incident.severity,
        "reported_at": str(incident.reported_at),
    }


@router.get("/trips/{trip_id}/incidents")
async def list_trip_incidents(trip_id: uuid.UUID, db: DBSession, driver: Driver = DriverDep):
    trip_q = await db.execute(select(Trip.driver_id).where(Trip.id == trip_id))
    trip_driver = trip_q.scalar_one_or_none()
    if trip_driver is None:
        raise NotFoundError("Trip not found")
    if trip_driver != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    result = await db.execute(
        select(TripIncident).where(TripIncident.trip_id == trip_id).order_by(TripIncident.reported_at.desc())
    )
    incidents = result.scalars().all()
    return {
        "items": [
            {
                "id": str(i.id),
                "type": i.type,
                "description": i.description,
                "severity": i.severity,
                "reported_at": str(i.reported_at),
                "resolved_at": str(i.resolved_at) if i.resolved_at else None,
            }
            for i in incidents
        ]
    }


# ── Pre-trip Inspection ──


INSPECTION_ITEMS = [
    "tyres", "brakes", "lights", "oil", "coolant", "ac",
    "mirrors", "horn", "fire_extinguisher", "first_aid_kit", "seat_belts",
]


class InspectionItem(BaseModel):
    name: str
    status: str  # pass, fail
    notes: str | None = None


class SubmitInspectionRequest(BaseModel):
    items: list[InspectionItem]


@router.post("/trips/{trip_id}/inspection")
async def submit_inspection(
    trip_id: uuid.UUID, data: SubmitInspectionRequest, db: DBSession, driver: Driver = DriverDep
):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.driver_id != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    inspection = {
        "inspected_at": str(datetime.now(timezone.utc)),
        "driver_id": str(driver.id),
        "items": [{"name": item.name, "status": item.status, "notes": item.notes} for item in data.items],
        "passed": all(item.status == "pass" for item in data.items),
    }
    trip.inspection_data = inspection
    await db.flush()

    return inspection


@router.get("/trips/{trip_id}/inspection")
async def get_inspection(trip_id: uuid.UUID, db: DBSession, driver: Driver = DriverDep):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.driver_id != driver.id:
        raise ForbiddenError("This trip is not assigned to you")

    return trip.inspection_data or {"items": [], "passed": False, "inspected_at": None}
