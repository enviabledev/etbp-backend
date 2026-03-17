import uuid
from datetime import date, time, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import TripStatus, UserRole
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.route import Route
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.vehicle import VehicleType

router = APIRouter(prefix="/schedules", tags=["Admin - Schedules"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER))


class CreateScheduleRequest(BaseModel):
    route_id: uuid.UUID
    vehicle_type_id: uuid.UUID
    departure_time: time
    recurrence: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    price_override: float | None = None


class UpdateScheduleRequest(BaseModel):
    departure_time: time | None = None
    recurrence: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    price_override: float | None = None
    is_active: bool | None = None


class CreateTripRequest(BaseModel):
    schedule_id: uuid.UUID | None = None
    route_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    departure_date: date
    departure_time: time
    price: float
    total_seats: int
    generate_seats: bool = Field(True, description="Auto-generate seat map")


class UpdateTripRequest(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    price: float | None = None
    status: TripStatus | None = None
    notes: str | None = None


class AssignTripRequest(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None


# ── Schedules ──


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_schedule(data: CreateScheduleRequest, db: DBSession):
    schedule = Schedule(**data.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.get("", dependencies=[AdminUser])
async def list_schedules(
    db: DBSession,
    route_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Schedule).options(
        selectinload(Schedule.route),
        selectinload(Schedule.vehicle_type),
    )
    if route_id:
        query = query.where(Schedule.route_id == route_id)
    if is_active is not None:
        query = query.where(Schedule.is_active == is_active)

    query = query.order_by(Schedule.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/{schedule_id}", dependencies=[AdminUser])
async def update_schedule(schedule_id: uuid.UUID, data: UpdateScheduleRequest, db: DBSession):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise NotFoundError("Schedule not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        if isinstance(value, TripStatus):
            value = value.value
        setattr(schedule, field, value)
    await db.flush()
    await db.refresh(schedule)
    return schedule


# ── Trips ──


@router.post("/trips", status_code=201, dependencies=[AdminUser])
async def create_trip(data: CreateTripRequest, db: DBSession):
    trip_data = data.model_dump(exclude={"generate_seats"})
    trip = Trip(**trip_data, available_seats=data.total_seats)
    db.add(trip)
    await db.flush()

    if data.generate_seats:
        # Try to find vehicle type via schedule
        vtype = None
        if data.schedule_id:
            sched = await db.execute(
                select(Schedule).options(selectinload(Schedule.vehicle_type))
                .where(Schedule.id == data.schedule_id)
            )
            s = sched.scalar_one_or_none()
            if s and s.vehicle_type:
                vtype = s.vehicle_type

        if vtype:
            seats = _generate_seats_from_layout(trip.id, vtype)
        else:
            # Fallback: simple numbered grid
            seats = []
            cols = 3
            for i in range(1, data.total_seats + 1):
                row = (i - 1) // cols + 1
                col = (i - 1) % cols + 1
                seats.append(TripSeat(
                    trip_id=trip.id,
                    seat_number=str(i),
                    seat_row=row,
                    seat_column=col,
                    seat_type="window" if col in (1, cols) else "aisle",
                ))
        for seat in seats:
            db.add(seat)
        await db.flush()

    await db.refresh(trip)
    return trip


@router.get("/trips", dependencies=[AdminUser])
async def list_trips(
    db: DBSession,
    route_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    status: TripStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Trip).options(
        selectinload(Trip.route),
        selectinload(Trip.vehicle),
        selectinload(Trip.driver),
    )
    if route_id:
        query = query.where(Trip.route_id == route_id)
    if from_date:
        query = query.where(Trip.departure_date >= from_date)
    if to_date:
        query = query.where(Trip.departure_date <= to_date)
    if status:
        query = query.where(Trip.status == status.value)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar()

    query = query.order_by(Trip.departure_date.desc(), Trip.departure_time.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/trips/{trip_id}", dependencies=[AdminUser])
async def get_trip(trip_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Trip)
        .options(
            selectinload(Trip.route),
            selectinload(Trip.vehicle),
            selectinload(Trip.driver),
            selectinload(Trip.seats),
            selectinload(Trip.bookings),
        )
        .where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    return trip


@router.put("/trips/{trip_id}", dependencies=[AdminUser])
async def update_trip(trip_id: uuid.UUID, data: UpdateTripRequest, db: DBSession):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        if isinstance(value, TripStatus):
            value = value.value
        setattr(trip, field, value)
    await db.flush()
    await db.refresh(trip)
    return trip


@router.put("/trips/{trip_id}/assign", dependencies=[AdminUser])
async def assign_vehicle_driver(trip_id: uuid.UUID, data: AssignTripRequest, db: DBSession):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")

    # Driver conflict check: same driver on another trip at same date+time
    if data.driver_id is not None:
        conflict = await db.execute(
            select(Trip).where(
                Trip.driver_id == data.driver_id,
                Trip.departure_date == trip.departure_date,
                Trip.departure_time == trip.departure_time,
                Trip.id != trip_id,
                Trip.status.notin_(["cancelled", "arrived"]),
            )
        )
        if conflict.scalar_one_or_none():
            raise ConflictError(
                "Driver is already assigned to another trip at the same date and time"
            )
        trip.driver_id = data.driver_id

    if data.vehicle_id is not None:
        trip.vehicle_id = data.vehicle_id

    await db.flush()
    return {
        "trip_id": str(trip.id),
        "vehicle_id": str(trip.vehicle_id) if trip.vehicle_id else None,
        "driver_id": str(trip.driver_id) if trip.driver_id else None,
    }


# ── Regenerate Seats ──


@router.post("/trips/{trip_id}/regenerate-seats", dependencies=[AdminUser])
async def regenerate_trip_seats(trip_id: uuid.UUID, db: DBSession):
    """Delete all existing seats and regenerate from vehicle type layout.
    Fails if any seats are booked or locked."""
    from app.core.constants import SeatStatus

    result = await db.execute(
        select(Trip)
        .options(
            selectinload(Trip.seats),
            selectinload(Trip.schedule).selectinload(Schedule.vehicle_type),
        )
        .where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")

    # Check for booked/locked seats
    has_booked = any(
        s.status in (SeatStatus.BOOKED, SeatStatus.LOCKED)
        for s in trip.seats
    )
    if has_booked:
        raise BadRequestError(
            "Cannot regenerate seats: some seats are booked or locked"
        )

    # Get vehicle type from schedule
    vtype = trip.schedule.vehicle_type if trip.schedule else None
    if not vtype:
        raise BadRequestError(
            "Trip has no schedule or vehicle type. Cannot regenerate seats."
        )

    # Delete old seats
    for seat in list(trip.seats):
        await db.delete(seat)
    await db.flush()

    # Create new seats
    new_seats = _generate_seats_from_layout(trip.id, vtype)
    for seat in new_seats:
        db.add(seat)

    trip.total_seats = len(new_seats)
    trip.available_seats = len(new_seats)
    await db.flush()

    return {
        "trip_id": str(trip.id),
        "total_seats": len(new_seats),
        "available_seats": len(new_seats),
        "message": f"Regenerated {len(new_seats)} seats from vehicle type layout",
    }


# ── Bulk Trip Generation ──


class GenerateTripsRequest(BaseModel):
    schedule_id: uuid.UUID
    from_date: date
    to_date: date


def _generate_seats_from_layout(trip_id: uuid.UUID, vehicle_type: VehicleType) -> list[TripSeat]:
    """Generate TripSeat objects dynamically from vehicle_type.seat_layout.

    seat_layout is the SINGLE SOURCE OF TRUTH. Format:
    {
        "columns": 3,
        "rows": [
            {"row": 1, "seats": [{"number": 1, "col": 1, "type": "window"}, ...]},
            ...
        ]
    }

    If seat_layout has no "rows" key, falls back to generating a simple
    grid from seat_capacity and columns.
    """
    layout = vehicle_type.seat_layout or {}
    rows = layout.get("rows")

    if rows:
        # Dynamic: read seats directly from layout JSON
        seats = []
        for row_data in rows:
            row_num = row_data["row"]
            for seat_data in row_data.get("seats", []):
                seats.append(TripSeat(
                    trip_id=trip_id,
                    seat_number=str(seat_data["number"]),
                    seat_row=row_num,
                    seat_column=seat_data.get("col", 1),
                    seat_type=seat_data.get("type", "aisle"),
                ))
        return seats

    # Fallback: generate simple grid from capacity + columns
    columns = layout.get("columns", 3)
    capacity = vehicle_type.seat_capacity
    seats = []
    seat_num = 0
    row = 1
    while seat_num < capacity:
        for col in range(1, columns + 1):
            seat_num += 1
            if seat_num > capacity:
                break
            seat_type = "window" if col in (1, columns) else "aisle"
            seats.append(TripSeat(
                trip_id=trip_id,
                seat_number=str(seat_num),
                seat_row=row,
                seat_column=col,
                seat_type=seat_type,
            ))
        row += 1
    return seats


@router.post("/trips/generate", status_code=201, dependencies=[AdminUser])
async def generate_trips_from_schedule(data: GenerateTripsRequest, db: DBSession):
    """Generate trips for a schedule over a date range, with seat maps from vehicle type layout."""
    schedule_result = await db.execute(
        select(Schedule)
        .options(selectinload(Schedule.vehicle_type), selectinload(Schedule.route))
        .where(Schedule.id == data.schedule_id)
    )
    schedule = schedule_result.scalar_one_or_none()
    if not schedule:
        raise NotFoundError("Schedule not found")
    if not schedule.is_active:
        raise BadRequestError("Schedule is not active")

    if data.from_date > data.to_date:
        raise BadRequestError("from_date must be before to_date")
    if (data.to_date - data.from_date).days > 90:
        raise BadRequestError("Cannot generate more than 90 days of trips at once")

    vehicle_type = schedule.vehicle_type
    route = schedule.route
    price = float(schedule.price_override) if schedule.price_override else float(route.base_price)

    # Parse recurrence
    recurrence = (schedule.recurrence or "daily").lower()
    if recurrence == "daily":
        valid_days = set(range(7))
    else:
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        valid_days = set()
        for part in recurrence.split(","):
            part = part.strip().lower()[:3]
            if part in day_map:
                valid_days.add(day_map[part])

    created = 0
    skipped = 0
    current = data.from_date
    while current <= data.to_date:
        if current.weekday() not in valid_days:
            current += timedelta(days=1)
            continue

        # Check if trip already exists for this schedule + date
        existing = await db.execute(
            select(Trip.id).where(
                Trip.schedule_id == schedule.id,
                Trip.departure_date == current,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            current += timedelta(days=1)
            continue

        trip = Trip(
            schedule_id=schedule.id,
            route_id=schedule.route_id,
            departure_date=current,
            departure_time=schedule.departure_time,
            price=price,
            total_seats=vehicle_type.seat_capacity,
            available_seats=vehicle_type.seat_capacity,
        )
        db.add(trip)
        await db.flush()

        seats = _generate_seats_from_layout(trip.id, vehicle_type)
        for seat in seats:
            db.add(seat)

        created += 1
        current += timedelta(days=1)

    await db.flush()

    return {
        "schedule_id": str(schedule.id),
        "route": route.name,
        "from_date": str(data.from_date),
        "to_date": str(data.to_date),
        "trips_created": created,
        "trips_skipped": skipped,
        "seats_per_trip": vehicle_type.seat_capacity,
    }
