import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import TripStatus, UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.schedule import Schedule, Trip, TripSeat

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
        for i in range(1, data.total_seats + 1):
            row = (i - 1) // 4 + 1
            col = (i - 1) % 4 + 1
            seat_type = "window" if col in (1, 4) else "aisle"
            seat = TripSeat(
                trip_id=trip.id,
                seat_number=f"{chr(64 + row)}{col}",
                seat_row=row,
                seat_column=col,
                seat_type=seat_type,
            )
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
    if data.vehicle_id is not None:
        trip.vehicle_id = data.vehicle_id
    if data.driver_id is not None:
        trip.driver_id = data.driver_id
    await db.flush()
    return {
        "trip_id": str(trip.id),
        "vehicle_id": str(trip.vehicle_id) if trip.vehicle_id else None,
        "driver_id": str(trip.driver_id) if trip.driver_id else None,
    }
