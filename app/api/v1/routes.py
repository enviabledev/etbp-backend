import uuid
from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.dependencies import DBSession
from app.models.route import Route, Terminal
from app.schemas.route import (
    PopularRouteResponse,
    RouteDetailResponse,
    RouteResponse,
    TerminalBriefResponse,
    TerminalResponse,
    TripSearchResult,
)
from app.services import route_service

router = APIRouter(prefix="/routes", tags=["Routes"])


@router.get("/search", response_model=list[TripSearchResult])
async def search_trips(
    db: DBSession,
    origin: str | None = Query(None, description="Origin city name or terminal code"),
    destination: str | None = Query(None, description="Destination city name or terminal code"),
    date: date | None = Query(None, alias="date", description="Departure date (YYYY-MM-DD)"),
    passengers: int = Query(1, ge=1, le=10, description="Number of passengers"),
):
    """Search available trips by origin, destination, date, and passenger count."""
    return await route_service.search_available_trips(
        db,
        origin=origin,
        destination=destination,
        departure_date=date,
        passengers=passengers,
    )


@router.get("/popular", response_model=list[PopularRouteResponse])
async def get_popular_routes(
    db: DBSession,
    limit: int = Query(10, ge=1, le=20),
):
    """Top routes by booking volume in the last 30 days."""
    return await route_service.get_popular_routes(db, limit=limit)


@router.get("/terminals", response_model=list[TerminalResponse])
async def list_terminals(
    db: DBSession,
    city: str | None = None,
    state: str | None = None,
    is_active: bool = True,
):
    query = select(Terminal).where(Terminal.is_active == is_active)
    if city:
        query = query.where(Terminal.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Terminal.state.ilike(f"%{state}%"))
    result = await db.execute(query.order_by(Terminal.name))
    return result.scalars().all()


@router.get("", response_model=list[RouteResponse])
async def list_routes(
    db: DBSession,
    origin: str | None = Query(None, description="Origin city or terminal code"),
    destination: str | None = Query(None, description="Destination city or terminal code"),
    is_active: bool = True,
):
    query = (
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
        )
        .where(Route.is_active == is_active)
    )
    if origin:
        origin_ids = select(Terminal.id).where(
            Terminal.city.ilike(f"%{origin}%") | (Terminal.code == origin.upper())
        )
        query = query.where(Route.origin_terminal_id.in_(origin_ids))
    if destination:
        dest_ids = select(Terminal.id).where(
            Terminal.city.ilike(f"%{destination}%") | (Terminal.code == destination.upper())
        )
        query = query.where(Route.destination_terminal_id.in_(dest_ids))
    result = await db.execute(query.order_by(Route.name))
    return result.scalars().all()


@router.get("/{route_id}/map")
async def get_route_map(route_id: uuid.UUID, db: DBSession):
    """Get route geographic data for map rendering."""
    from app.models.route import RouteStop

    result = await db.execute(
        select(Route).options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
            selectinload(Route.stops).selectinload(RouteStop.terminal),
        ).where(Route.id == route_id)
    )
    route = result.scalar_one_or_none()
    if not route:
        raise NotFoundError("Route not found")

    stops = []
    for stop in sorted(route.stops, key=lambda s: s.stop_order):
        t = stop.terminal
        if t:
            stops.append({
                "name": t.name, "city": t.city,
                "latitude": float(t.latitude) if t.latitude else None,
                "longitude": float(t.longitude) if t.longitude else None,
                "estimated_arrival_minutes": stop.duration_from_origin_minutes,
                "order": stop.stop_order,
            })

    o = route.origin_terminal
    d = route.destination_terminal
    return {
        "route_id": str(route.id),
        "route_name": route.name,
        "distance_km": route.distance_km,
        "estimated_duration_minutes": route.estimated_duration_minutes,
        "origin": {
            "name": o.name, "city": o.city, "address": o.address,
            "latitude": float(o.latitude) if o.latitude else None,
            "longitude": float(o.longitude) if o.longitude else None,
        } if o else None,
        "destination": {
            "name": d.name, "city": d.city, "address": d.address,
            "latitude": float(d.latitude) if d.latitude else None,
            "longitude": float(d.longitude) if d.longitude else None,
        } if d else None,
        "stops": stops,
    }


@router.get("/{route_id}", response_model=RouteDetailResponse)
async def get_route(route_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
            selectinload(Route.stops),
        )
        .where(Route.id == route_id)
    )
    route = result.scalar_one_or_none()
    if not route:
        raise NotFoundError("Route not found")
    return route
