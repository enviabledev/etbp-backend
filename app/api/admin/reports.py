import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.constants import UserRole
from app.dependencies import DBSession, require_role
from app.services import analytics_service

router = APIRouter(prefix="/reports", tags=["Admin - Reports"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("/dashboard", dependencies=[AdminUser])
async def dashboard_summary(db: DBSession):
    """Aggregated KPIs: users, bookings, revenue, active trips."""
    return await analytics_service.get_dashboard_stats(db)


@router.get("/bookings-by-status", dependencies=[AdminUser])
async def bookings_by_status(db: DBSession):
    """Booking count grouped by status."""
    return await analytics_service.get_bookings_by_status(db)


@router.get("/revenue", dependencies=[AdminUser])
async def revenue_report(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
):
    """Revenue over time, grouped by day/week/month."""
    return await analytics_service.get_revenue_by_date(
        db, from_date=from_date, to_date=to_date, group_by=group_by
    )


@router.get("/revenue-by-route", dependencies=[AdminUser])
async def revenue_by_route(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(20, ge=1, le=50),
):
    """Revenue breakdown per route."""
    return await analytics_service.get_revenue_by_route(
        db, from_date=from_date, to_date=to_date, limit=limit
    )


@router.get("/occupancy", dependencies=[AdminUser])
async def occupancy_report(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    route_id: uuid.UUID | None = None,
):
    """Seat occupancy per trip."""
    return await analytics_service.get_occupancy_stats(
        db, from_date=from_date, to_date=to_date, route_id=route_id
    )


@router.get("/user-growth", dependencies=[AdminUser])
async def user_growth(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
):
    """New user registrations over time."""
    return await analytics_service.get_user_growth(
        db, from_date=from_date, to_date=to_date, group_by=group_by
    )


@router.get("/booking-trends", dependencies=[AdminUser])
async def booking_trends(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
):
    """Booking volume with confirmed/cancelled/pending breakdown over time."""
    return await analytics_service.get_booking_trends(
        db, from_date=from_date, to_date=to_date, group_by=group_by
    )


@router.get("/top-routes", dependencies=[AdminUser])
async def top_routes(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(10, ge=1, le=50),
):
    """Top routes by booking count with revenue and avg booking value."""
    return await analytics_service.get_top_routes(
        db, from_date=from_date, to_date=to_date, limit=limit
    )


@router.get("/payment-methods", dependencies=[AdminUser])
async def payment_method_breakdown(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """Payment count and revenue grouped by payment method."""
    return await analytics_service.get_payment_method_breakdown(
        db, from_date=from_date, to_date=to_date
    )
