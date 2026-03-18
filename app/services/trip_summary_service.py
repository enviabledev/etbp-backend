import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.booking import Booking
from app.models.schedule import Trip, TripIncident

logger = logging.getLogger(__name__)


async def generate_trip_summary(db: AsyncSession, trip_id) -> dict:
    result = await db.execute(
        select(Trip).options(
            selectinload(Trip.route),
        ).where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        return {}

    # Passenger stats
    booking_q = await db.execute(
        select(
            func.count(Booking.id).label("total"),
            func.count(Booking.id).filter(Booking.checked_in_at.isnot(None)).label("checked_in"),
        ).where(
            Booking.trip_id == trip_id,
            Booking.status.in_(["confirmed", "checked_in", "completed"]),
        )
    )
    counts = booking_q.one()
    total_booked = counts.total or 0
    checked_in = counts.checked_in or 0
    no_shows = max(0, total_booked - checked_in)

    # Revenue
    rev_q = await db.execute(
        select(func.sum(Booking.total_amount)).where(
            Booking.trip_id == trip_id,
            Booking.status.in_(["confirmed", "checked_in", "completed"]),
        )
    )
    total_revenue = float(rev_q.scalar() or 0)

    # Timing
    scheduled = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    actual_dep = trip.actual_departure_at
    actual_arr = trip.actual_arrival_at

    delay_minutes = 0
    on_time = True
    duration_minutes = 0
    if actual_dep:
        delay_minutes = max(0, (actual_dep - scheduled).total_seconds() / 60)
        on_time = delay_minutes <= 5
    if actual_dep and actual_arr:
        duration_minutes = (actual_arr - actual_dep).total_seconds() / 60

    # Incidents
    inc_q = await db.execute(
        select(TripIncident.type, func.count(TripIncident.id))
        .where(TripIncident.trip_id == trip_id)
        .group_by(TripIncident.type)
    )
    incidents = inc_q.all()
    incident_count = sum(r[1] for r in incidents)
    incident_types = [r[0] for r in incidents]

    # Inspection
    inspection_completed = trip.inspection_data is not None
    inspection_passed = False
    if inspection_completed and isinstance(trip.inspection_data, dict):
        inspection_passed = trip.inspection_data.get("passed", False)

    # Performance score
    score = 100
    if not on_time:
        score -= 15
    if total_booked > 0:
        no_show_rate = no_shows / total_booked
        if no_show_rate > 0.2:
            score -= 10
    score -= incident_count * 10
    score = max(0, min(100, score))

    route_name = trip.route.name if trip.route else "Unknown"

    return {
        "trip_id": str(trip_id),
        "route_name": route_name,
        "departure_date": str(trip.departure_date),
        "passengers": {
            "total_booked": total_booked,
            "checked_in": checked_in,
            "no_shows": no_shows,
            "occupancy_rate": round(total_booked / max(trip.total_seats, 1) * 100, 1),
        },
        "timing": {
            "scheduled_departure": str(scheduled),
            "actual_departure": str(actual_dep) if actual_dep else None,
            "actual_arrival": str(actual_arr) if actual_arr else None,
            "on_time": on_time,
            "delay_minutes": round(delay_minutes, 1),
            "trip_duration_minutes": round(duration_minutes, 1),
        },
        "revenue": {"total": total_revenue, "currency": "NGN"},
        "incidents": {"count": incident_count, "types": incident_types},
        "inspection": {"completed": inspection_completed, "all_passed": inspection_passed},
        "performance_score": score,
    }
