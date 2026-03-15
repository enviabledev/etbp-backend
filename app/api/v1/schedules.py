import uuid
from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.dependencies import DBSession
from app.models.schedule import Trip
from app.schemas.route import TripSearchResult
from app.schemas.schedule import TripDetailResponse, TripResponse
from app.services import route_service

router = APIRouter(prefix="/trips", tags=["Trips"])


@router.get("", response_model=list[TripSearchResult])
async def list_trips(
    db: DBSession,
    route_id: uuid.UUID | None = Query(None, description="Filter by route"),
    date: date | None = Query(None, description="Filter by departure date"),
    passengers: int = Query(1, ge=1, le=10),
):
    """List available trips for a route and date. Returns trips with route & vehicle details."""
    return await route_service.search_available_trips(
        db,
        departure_date=date,
        passengers=passengers,
        # Build origin/destination filter from route_id if provided
        origin=None,
        destination=None,
        route_id=route_id,
    )


@router.get("/{trip_id}", response_model=TripDetailResponse)
async def get_trip(trip_id: uuid.UUID, db: DBSession):
    """Get trip details including all seat information."""
    result = await db.execute(
        select(Trip).options(selectinload(Trip.seats)).where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    return trip
