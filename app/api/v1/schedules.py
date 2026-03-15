import uuid
from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.models.schedule import Trip, TripSeat
from app.schemas.schedule import TripDetailResponse, TripResponse

router = APIRouter(prefix="/schedules", tags=["Schedules"])


@router.get("/trips", response_model=list[TripResponse])
async def search_trips(
    db: DBSession,
    route_id: uuid.UUID | None = None,
    departure_date: date | None = None,
    status: str | None = None,
):
    query = select(Trip)
    if route_id:
        query = query.where(Trip.route_id == route_id)
    if departure_date:
        query = query.where(Trip.departure_date == departure_date)
    if status:
        query = query.where(Trip.status == status)
    query = query.where(Trip.available_seats > 0).order_by(
        Trip.departure_date, Trip.departure_time
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/trips/{trip_id}", response_model=TripDetailResponse)
async def get_trip(trip_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Trip).options(selectinload(Trip.seats)).where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    return trip
