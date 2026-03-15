import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.schedule import Trip


async def search_trips(
    db: AsyncSession,
    route_id: uuid.UUID | None = None,
    departure_date: date | None = None,
) -> list[Trip]:
    query = select(Trip).where(Trip.available_seats > 0)
    if route_id:
        query = query.where(Trip.route_id == route_id)
    if departure_date:
        query = query.where(Trip.departure_date == departure_date)
    query = query.order_by(Trip.departure_date, Trip.departure_time)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_trip_with_seats(db: AsyncSession, trip_id: uuid.UUID) -> Trip:
    result = await db.execute(
        select(Trip).options(selectinload(Trip.seats)).where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    return trip
