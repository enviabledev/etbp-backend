import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.booking import Booking
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip
from app.models.vehicle import VehicleType


async def list_active_terminals(
    db: AsyncSession,
    search: str | None = None,
) -> list[Terminal]:
    query = select(Terminal).where(Terminal.is_active == True)  # noqa: E712
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Terminal.name.ilike(pattern)
            | Terminal.city.ilike(pattern)
            | Terminal.code.ilike(pattern)
        )
    result = await db.execute(query.order_by(Terminal.city, Terminal.name))
    return list(result.scalars().all())


async def search_available_trips(
    db: AsyncSession,
    origin: str | None = None,
    destination: str | None = None,
    departure_date: date | None = None,
    passengers: int = 1,
    route_id: uuid.UUID | None = None,
) -> list[dict]:
    """Search trips with route details and vehicle type info."""
    query = (
        select(Trip)
        .join(Route, Trip.route_id == Route.id)
        .outerjoin(Schedule, Trip.schedule_id == Schedule.id)
        .outerjoin(VehicleType, Schedule.vehicle_type_id == VehicleType.id)
        .options(
            selectinload(Trip.route).selectinload(Route.origin_terminal),
            selectinload(Trip.route).selectinload(Route.destination_terminal),
            selectinload(Trip.schedule).selectinload(Schedule.vehicle_type),
        )
        .where(
            Trip.available_seats >= passengers,
            Trip.status.in_(["scheduled", "boarding"]),
            Route.is_active == True,  # noqa: E712
        )
    )

    if route_id:
        query = query.where(Trip.route_id == route_id)

    if departure_date:
        query = query.where(Trip.departure_date == departure_date)
    else:
        query = query.where(Trip.departure_date >= date.today())

    if origin:
        origin_terminal_ids = select(Terminal.id).where(
            Terminal.is_active == True,  # noqa: E712
            Terminal.city.ilike(f"%{origin}%") | (Terminal.code == origin.upper()),
        )
        query = query.where(Route.origin_terminal_id.in_(origin_terminal_ids))

    if destination:
        dest_terminal_ids = select(Terminal.id).where(
            Terminal.is_active == True,  # noqa: E712
            Terminal.city.ilike(f"%{destination}%") | (Terminal.code == destination.upper()),
        )
        query = query.where(Route.destination_terminal_id.in_(dest_terminal_ids))

    query = query.order_by(Trip.departure_date, Trip.departure_time)

    result = await db.execute(query)
    trips_orm = result.scalars().all()

    trips = []
    for trip in trips_orm:
        r = trip.route
        vtype = trip.schedule.vehicle_type if trip.schedule else None
        trips.append({
            "id": trip.id,
            "route": {
                "id": r.id,
                "name": r.name,
                "code": r.code,
                "origin_terminal": {
                    "id": r.origin_terminal.id,
                    "name": r.origin_terminal.name,
                    "code": r.origin_terminal.code,
                    "city": r.origin_terminal.city,
                    "state": r.origin_terminal.state,
                },
                "destination_terminal": {
                    "id": r.destination_terminal.id,
                    "name": r.destination_terminal.name,
                    "code": r.destination_terminal.code,
                    "city": r.destination_terminal.city,
                    "state": r.destination_terminal.state,
                },
                "distance_km": r.distance_km,
                "estimated_duration_minutes": r.estimated_duration_minutes,
                "base_price": float(r.base_price),
                "currency": r.currency,
            },
            "vehicle_type": {
                "id": vtype.id,
                "name": vtype.name,
                "seat_capacity": vtype.seat_capacity,
                "amenities": vtype.amenities,
            } if vtype else None,
            "departure_date": trip.departure_date,
            "departure_time": trip.departure_time,
            "status": trip.status,
            "price": float(trip.price),
            "currency": r.currency,
            "available_seats": trip.available_seats,
            "total_seats": trip.total_seats,
            "estimated_duration_minutes": r.estimated_duration_minutes,
        })

    return trips


async def get_popular_routes(db: AsyncSession, limit: int = 10) -> list[dict]:
    """Top routes by booking count in the last 30 days."""
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    query = (
        select(
            Route,
            func.count(Booking.id).label("booking_count"),
        )
        .join(Trip, Trip.route_id == Route.id)
        .join(Booking, Booking.trip_id == Trip.id)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
        )
        .where(
            Route.is_active == True,  # noqa: E712
            Booking.created_at >= thirty_days_ago,
            Booking.status.notin_(["cancelled", "expired"]),
        )
        .group_by(Route.id)
        .order_by(func.count(Booking.id).desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    popular = []
    for route, count in rows:
        popular.append({
            "route": {
                "id": route.id,
                "name": route.name,
                "code": route.code,
                "origin_terminal": {
                    "id": route.origin_terminal.id,
                    "name": route.origin_terminal.name,
                    "code": route.origin_terminal.code,
                    "city": route.origin_terminal.city,
                    "state": route.origin_terminal.state,
                },
                "destination_terminal": {
                    "id": route.destination_terminal.id,
                    "name": route.destination_terminal.name,
                    "code": route.destination_terminal.code,
                    "city": route.destination_terminal.city,
                    "state": route.destination_terminal.state,
                },
                "distance_km": route.distance_km,
                "estimated_duration_minutes": route.estimated_duration_minutes,
                "base_price": float(route.base_price),
                "currency": route.currency,
            },
            "booking_count": count,
        })

    return popular
