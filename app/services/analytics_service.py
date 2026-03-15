import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Integer, case, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.payment import Payment
from app.models.route import Route, Terminal
from app.models.schedule import Trip
from app.models.user import User


async def get_dashboard_stats(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    today = date.today()
    month_start = today.replace(day=1)
    last_30 = now - timedelta(days=30)

    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    new_users_30d = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at >= last_30)
        )
    ).scalar() or 0

    total_bookings = (await db.execute(select(func.count(Booking.id)))).scalar() or 0
    bookings_today = (
        await db.execute(
            select(func.count(Booking.id)).where(
                func.date(Booking.created_at) == today
            )
        )
    ).scalar() or 0
    bookings_this_month = (
        await db.execute(
            select(func.count(Booking.id)).where(
                func.date(Booking.created_at) >= month_start
            )
        )
    ).scalar() or 0

    total_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "successful"
            )
        )
    ).scalar() or 0
    revenue_today = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "successful",
                func.date(Payment.paid_at) == today,
            )
        )
    ).scalar() or 0
    revenue_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "successful",
                func.date(Payment.paid_at) >= month_start,
            )
        )
    ).scalar() or 0

    active_trips = (
        await db.execute(
            select(func.count(Trip.id)).where(
                Trip.status.in_(["scheduled", "boarding", "en_route"]),
                Trip.departure_date >= today,
            )
        )
    ).scalar() or 0

    cancelled_bookings = (
        await db.execute(
            select(func.count(Booking.id)).where(
                Booking.status == "cancelled",
                Booking.cancelled_at >= last_30,
            )
        )
    ).scalar() or 0

    return {
        "users": {
            "total": total_users,
            "new_last_30_days": new_users_30d,
        },
        "bookings": {
            "total": total_bookings,
            "today": bookings_today,
            "this_month": bookings_this_month,
            "cancelled_last_30_days": cancelled_bookings,
        },
        "revenue": {
            "total": float(total_revenue),
            "today": float(revenue_today),
            "this_month": float(revenue_this_month),
        },
        "trips": {
            "active": active_trips,
        },
    }


async def get_bookings_by_status(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(
            Booking.status,
            func.count(Booking.id).label("count"),
        ).group_by(Booking.status)
    )
    return [{"status": row.status, "count": row.count} for row in result.all()]


async def get_revenue_by_date(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = "day",
) -> list[dict]:
    if group_by == "month":
        date_label = func.date_trunc("month", Payment.paid_at).label("period")
    elif group_by == "week":
        date_label = func.date_trunc("week", Payment.paid_at).label("period")
    else:
        date_label = func.date(Payment.paid_at).label("period")

    query = select(
        date_label,
        func.sum(Payment.amount).label("amount"),
        func.count(Payment.id).label("count"),
    ).where(Payment.status == "successful")

    if from_date:
        query = query.where(func.date(Payment.paid_at) >= from_date)
    if to_date:
        query = query.where(func.date(Payment.paid_at) <= to_date)

    query = query.group_by("period").order_by(date_label.desc())
    result = await db.execute(query)
    return [
        {"period": str(row.period), "amount": float(row.amount), "count": row.count}
        for row in result.all()
    ]


async def get_revenue_by_route(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 20,
) -> list[dict]:
    query = (
        select(
            Route.id,
            Route.name,
            Route.code,
            func.sum(Payment.amount).label("revenue"),
            func.count(Booking.id).label("booking_count"),
        )
        .join(Booking, Payment.booking_id == Booking.id)
        .join(Trip, Booking.trip_id == Trip.id)
        .join(Route, Trip.route_id == Route.id)
        .where(Payment.status == "successful")
    )
    if from_date:
        query = query.where(func.date(Payment.paid_at) >= from_date)
    if to_date:
        query = query.where(func.date(Payment.paid_at) <= to_date)

    query = (
        query.group_by(Route.id, Route.name, Route.code)
        .order_by(func.sum(Payment.amount).desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return [
        {
            "route_id": str(row.id),
            "route_name": row.name,
            "route_code": row.code,
            "revenue": float(row.revenue),
            "booking_count": row.booking_count,
        }
        for row in result.all()
    ]


async def get_occupancy_stats(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    route_id: uuid.UUID | None = None,
) -> list[dict]:
    query = select(
        Trip.id,
        Trip.departure_date,
        Trip.departure_time,
        Trip.total_seats,
        Trip.available_seats,
        Trip.status,
        Route.name.label("route_name"),
        Route.code.label("route_code"),
    ).join(Route, Trip.route_id == Route.id)

    if from_date:
        query = query.where(Trip.departure_date >= from_date)
    if to_date:
        query = query.where(Trip.departure_date <= to_date)
    if route_id:
        query = query.where(Trip.route_id == route_id)

    query = query.order_by(Trip.departure_date.desc(), Trip.departure_time.desc()).limit(100)
    result = await db.execute(query)

    trips = []
    for row in result.all():
        booked = row.total_seats - row.available_seats
        occupancy = round(booked / row.total_seats * 100, 1) if row.total_seats > 0 else 0
        trips.append({
            "trip_id": str(row.id),
            "route_name": row.route_name,
            "route_code": row.route_code,
            "departure_date": str(row.departure_date),
            "departure_time": str(row.departure_time),
            "total_seats": row.total_seats,
            "booked_seats": booked,
            "available_seats": row.available_seats,
            "occupancy_percent": occupancy,
            "status": row.status,
        })
    return trips


async def get_user_growth(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = "day",
) -> list[dict]:
    if group_by == "month":
        date_label = func.date_trunc("month", User.created_at).label("period")
    elif group_by == "week":
        date_label = func.date_trunc("week", User.created_at).label("period")
    else:
        date_label = func.date(User.created_at).label("period")

    query = select(
        date_label,
        func.count(User.id).label("count"),
    )
    if from_date:
        query = query.where(func.date(User.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(User.created_at) <= to_date)

    query = query.group_by("period").order_by(date_label.desc())
    result = await db.execute(query)
    return [
        {"period": str(row.period), "count": row.count}
        for row in result.all()
    ]


async def get_booking_trends(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    group_by: str = "day",
) -> list[dict]:
    if group_by == "month":
        date_label = func.date_trunc("month", Booking.created_at).label("period")
    elif group_by == "week":
        date_label = func.date_trunc("week", Booking.created_at).label("period")
    else:
        date_label = func.date(Booking.created_at).label("period")

    query = select(
        date_label,
        func.count(Booking.id).label("total"),
        func.sum(case((Booking.status == "confirmed", 1), else_=0)).label("confirmed"),
        func.sum(case((Booking.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.sum(case((Booking.status == "pending", 1), else_=0)).label("pending"),
    )
    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    query = query.group_by("period").order_by(date_label.desc())
    result = await db.execute(query)
    return [
        {
            "period": str(row.period),
            "total": row.total,
            "confirmed": int(row.confirmed),
            "cancelled": int(row.cancelled),
            "pending": int(row.pending),
        }
        for row in result.all()
    ]


async def get_top_routes(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 10,
) -> list[dict]:
    query = (
        select(
            Route.id,
            Route.name,
            Route.code,
            func.count(Booking.id).label("booking_count"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("total_revenue"),
            func.coalesce(func.avg(Booking.total_amount), 0).label("avg_booking_value"),
        )
        .join(Trip, Trip.route_id == Route.id)
        .join(Booking, Booking.trip_id == Trip.id)
        .where(Booking.status.notin_(["cancelled", "expired"]))
    )
    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    query = (
        query.group_by(Route.id, Route.name, Route.code)
        .order_by(func.count(Booking.id).desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return [
        {
            "route_id": str(row.id),
            "route_name": row.name,
            "route_code": row.code,
            "booking_count": row.booking_count,
            "total_revenue": float(row.total_revenue),
            "avg_booking_value": round(float(row.avg_booking_value), 2),
        }
        for row in result.all()
    ]


async def get_payment_method_breakdown(
    db: AsyncSession,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[dict]:
    query = select(
        Payment.method,
        func.count(Payment.id).label("count"),
        func.sum(Payment.amount).label("total_amount"),
    ).where(Payment.status == "successful")

    if from_date:
        query = query.where(func.date(Payment.paid_at) >= from_date)
    if to_date:
        query = query.where(func.date(Payment.paid_at) <= to_date)

    query = query.group_by(Payment.method).order_by(func.sum(Payment.amount).desc())
    result = await db.execute(query)
    return [
        {
            "method": row.method,
            "count": row.count,
            "total_amount": float(row.total_amount),
        }
        for row in result.all()
    ]
