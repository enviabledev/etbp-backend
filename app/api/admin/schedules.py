import uuid
from datetime import date, time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.schedule import Schedule, Trip

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


class CreateTripRequest(BaseModel):
    schedule_id: uuid.UUID | None = None
    route_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    departure_date: date
    departure_time: time
    price: float
    total_seats: int


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_schedule(data: CreateScheduleRequest, db: DBSession):
    schedule = Schedule(**data.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.post("/trips", status_code=201, dependencies=[AdminUser])
async def create_trip(data: CreateTripRequest, db: DBSession):
    trip = Trip(**data.model_dump(), available_seats=data.total_seats)
    db.add(trip)
    await db.flush()
    await db.refresh(trip)
    return trip


@router.put("/trips/{trip_id}/status", dependencies=[AdminUser])
async def update_trip_status(trip_id: uuid.UUID, status: str, db: DBSession):
    result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    trip.status = status
    await db.flush()
    return {"id": str(trip.id), "status": trip.status}
