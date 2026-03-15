import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from app.core.constants import BookingStatus, UserRole
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.route import Route
from app.models.schedule import Trip
from app.models.user import User

router = APIRouter(prefix="/reports", tags=["Agent - Reports"])

AgentUser = Annotated[User, Depends(require_role(UserRole.AGENT, UserRole.ADMIN, UserRole.SUPER_ADMIN))]


@router.get("/dashboard")
async def agent_dashboard(
    db: DBSession,
    current_user: AgentUser,
):
    """Agent's daily dashboard: today's stats, active trips, pending bookings."""
    today = date.today()

    # Today's bookings by this agent
    today_stats = await db.execute(
        select(
            func.count(Booking.id).label("total"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("revenue"),
            func.sum(case((Booking.status == BookingStatus.CONFIRMED, 1), else_=0)).label("confirmed"),
            func.sum(case((Booking.status == BookingStatus.PENDING, 1), else_=0)).label("pending"),
            func.sum(case((Booking.status == BookingStatus.CANCELLED, 1), else_=0)).label("cancelled"),
            func.coalesce(func.sum(Booking.passenger_count), 0).label("passengers"),
        ).where(
            Booking.booked_by_user_id == current_user.id,
            func.date(Booking.created_at) == today,
        )
    )
    row = today_stats.one()

    # Active trips today
    active_trips = (
        await db.execute(
            select(func.count(Trip.id)).where(
                Trip.departure_date == today,
                Trip.status.in_(["scheduled", "boarding", "en_route"]),
            )
        )
    ).scalar() or 0

    return {
        "today": {
            "bookings": row.total,
            "revenue": float(row.revenue),
            "confirmed": int(row.confirmed or 0),
            "pending": int(row.pending or 0),
            "cancelled": int(row.cancelled or 0),
            "passengers": int(row.passengers or 0),
        },
        "active_trips_today": active_trips,
    }


@router.get("/my-bookings-summary")
async def my_booking_summary(
    db: DBSession,
    current_user: AgentUser,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """Agent's total booking stats over a period."""
    query = select(
        func.count(Booking.id).label("total"),
        func.coalesce(func.sum(Booking.total_amount), 0).label("total_amount"),
        func.coalesce(func.sum(Booking.passenger_count), 0).label("total_passengers"),
        func.sum(case((Booking.status == BookingStatus.CONFIRMED, 1), else_=0)).label("confirmed"),
        func.sum(case((Booking.status == BookingStatus.CANCELLED, 1), else_=0)).label("cancelled"),
    ).where(Booking.booked_by_user_id == current_user.id)

    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    result = await db.execute(query)
    row = result.one()
    return {
        "total_bookings": row.total,
        "total_amount": float(row.total_amount),
        "total_passengers": int(row.total_passengers or 0),
        "confirmed": int(row.confirmed or 0),
        "cancelled": int(row.cancelled or 0),
    }


@router.get("/daily-breakdown")
async def daily_breakdown(
    db: DBSession,
    current_user: AgentUser,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """Agent's bookings broken down by day."""
    if not from_date:
        from_date = date.today() - timedelta(days=30)
    if not to_date:
        to_date = date.today()

    query = select(
        func.date(Booking.created_at).label("date"),
        func.count(Booking.id).label("bookings"),
        func.coalesce(func.sum(Booking.total_amount), 0).label("revenue"),
        func.coalesce(func.sum(Booking.passenger_count), 0).label("passengers"),
    ).where(
        Booking.booked_by_user_id == current_user.id,
        func.date(Booking.created_at) >= from_date,
        func.date(Booking.created_at) <= to_date,
    ).group_by(func.date(Booking.created_at)).order_by(func.date(Booking.created_at).desc())

    result = await db.execute(query)
    return [
        {
            "date": str(row.date),
            "bookings": row.bookings,
            "revenue": float(row.revenue),
            "passengers": int(row.passengers),
        }
        for row in result.all()
    ]


@router.get("/top-routes")
async def agent_top_routes(
    db: DBSession,
    current_user: AgentUser,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(10, ge=1, le=20),
):
    """Routes this agent books the most."""
    query = (
        select(
            Route.name,
            Route.code,
            func.count(Booking.id).label("booking_count"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("revenue"),
        )
        .join(Trip, Booking.trip_id == Trip.id)
        .join(Route, Trip.route_id == Route.id)
        .where(
            Booking.booked_by_user_id == current_user.id,
            Booking.status.notin_(["cancelled", "expired"]),
        )
    )
    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    query = (
        query.group_by(Route.name, Route.code)
        .order_by(func.count(Booking.id).desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return [
        {
            "route_name": row.name,
            "route_code": row.code,
            "booking_count": row.booking_count,
            "revenue": float(row.revenue),
        }
        for row in result.all()
    ]


@router.get("/payment-methods")
async def agent_payment_methods(
    db: DBSession,
    current_user: AgentUser,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """Payment method breakdown for this agent's bookings."""
    query = (
        select(
            Payment.method,
            func.count(Payment.id).label("count"),
            func.sum(Payment.amount).label("total"),
        )
        .join(Booking, Payment.booking_id == Booking.id)
        .where(
            Booking.booked_by_user_id == current_user.id,
            Payment.status == "successful",
        )
    )
    if from_date:
        query = query.where(func.date(Payment.paid_at) >= from_date)
    if to_date:
        query = query.where(func.date(Payment.paid_at) <= to_date)

    query = query.group_by(Payment.method)
    result = await db.execute(query)
    return [
        {"method": row.method, "count": row.count, "total": float(row.total)}
        for row in result.all()
    ]
