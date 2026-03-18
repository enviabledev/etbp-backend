import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.booking import Booking
from app.models.schedule import Trip
from app.models.route import Route, Terminal
from app.models.user import User
from app.models.review import TripReview
from app.models.dashboard_widget import DashboardWidget

router = APIRouter(prefix="/analytics", tags=["Admin - Analytics"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


def _parse_dates(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    today = date.today()
    d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    d_to = date.fromisoformat(date_to) if date_to else today
    return d_from, d_to


@router.get("/revenue", dependencies=[AdminUser])
async def revenue_analytics(
    db: DBSession,
    date_from: str | None = None,
    date_to: str | None = None,
    route_id: uuid.UUID | None = None,
):
    d_from, d_to = _parse_dates(date_from, date_to)

    base = select(Booking).join(Trip, Booking.trip_id == Trip.id).where(
        Booking.status.in_(["confirmed", "checked_in", "completed"]),
        func.date(Booking.created_at) >= d_from,
        func.date(Booking.created_at) <= d_to,
    )
    if route_id:
        base = base.where(Trip.route_id == route_id)

    # Total
    total_q = await db.execute(select(func.sum(Booking.total_amount), func.count(Booking.id)).select_from(base.subquery()))
    total_row = total_q.one()
    total_revenue = float(total_row[0] or 0)
    total_bookings = total_row[1] or 0

    # By route
    route_q = await db.execute(
        select(Route.name, func.sum(Booking.total_amount).label("rev"), func.count(Booking.id))
        .join(Trip, Booking.trip_id == Trip.id)
        .join(Route, Trip.route_id == Route.id)
        .where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(d_from, d_to))
        .group_by(Route.name).order_by(func.sum(Booking.total_amount).desc()).limit(10)
    )
    by_route = [{"route_name": r[0], "revenue": float(r[1] or 0), "bookings": r[2]} for r in route_q.all()]

    # By payment method
    method_q = await db.execute(
        select(Booking.payment_method_hint, func.sum(Booking.total_amount), func.count(Booking.id))
        .where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(d_from, d_to))
        .group_by(Booking.payment_method_hint)
    )
    by_method = [{"method": r[0] or "unknown", "revenue": float(r[1] or 0), "count": r[2]} for r in method_q.all()]

    # Previous period comparison
    period_days = (d_to - d_from).days + 1
    prev_from = d_from - timedelta(days=period_days)
    prev_to = d_from - timedelta(days=1)
    prev_q = await db.execute(
        select(func.sum(Booking.total_amount)).where(
            Booking.status.in_(["confirmed", "checked_in", "completed"]),
            func.date(Booking.created_at).between(prev_from, prev_to),
        )
    )
    prev_revenue = float(prev_q.scalar() or 0)
    change_pct = round(((total_revenue - prev_revenue) / max(prev_revenue, 1)) * 100, 1) if prev_revenue else 0

    return {
        "period": {"from": str(d_from), "to": str(d_to)},
        "total_revenue": total_revenue, "total_bookings": total_bookings, "currency": "NGN",
        "avg_ticket": round(total_revenue / max(total_bookings, 1), 2),
        "by_route": by_route, "by_payment_method": by_method,
        "comparison": {"previous_period_revenue": prev_revenue, "change_percentage": change_pct},
    }


@router.get("/occupancy", dependencies=[AdminUser])
async def occupancy_analytics(
    db: DBSession,
    date_from: str | None = None,
    date_to: str | None = None,
    route_id: uuid.UUID | None = None,
):
    d_from, d_to = _parse_dates(date_from, date_to)

    query = select(
        Trip.departure_date,
        func.sum(Trip.total_seats).label("total"),
        func.sum(Trip.total_seats - Trip.available_seats).label("booked"),
    ).where(Trip.departure_date.between(d_from, d_to), Trip.status != "cancelled")
    if route_id:
        query = query.where(Trip.route_id == route_id)
    query = query.group_by(Trip.departure_date).order_by(Trip.departure_date)

    result = await db.execute(query)
    data = []
    total_seats_all = 0
    booked_seats_all = 0
    for row in result.all():
        total_s = int(row[1] or 0)
        booked_s = int(row[2] or 0)
        occ = round(booked_s / max(total_s, 1) * 100, 1)
        data.append({"label": str(row[0]), "occupancy_rate": occ, "total_seats": total_s, "booked_seats": booked_s})
        total_seats_all += total_s
        booked_seats_all += booked_s

    overall = round(booked_seats_all / max(total_seats_all, 1) * 100, 1)
    busiest = max(data, key=lambda x: x["occupancy_rate"]) if data else None
    quietest = min(data, key=lambda x: x["occupancy_rate"]) if data else None

    return {
        "period": {"from": str(d_from), "to": str(d_to)},
        "overall_occupancy_rate": overall, "data": data,
        "busiest": busiest, "quietest": quietest,
    }


@router.get("/customers", dependencies=[AdminUser])
async def customer_analytics(db: DBSession, date_from: str | None = None, date_to: str | None = None):
    d_from, d_to = _parse_dates(date_from, date_to)

    total_q = await db.execute(select(func.count(User.id)).where(User.role == "passenger", User.is_active == True))  # noqa: E712
    total_customers = total_q.scalar() or 0

    new_q = await db.execute(select(func.count(User.id)).where(User.role == "passenger", func.date(User.created_at).between(d_from, d_to)))
    new_customers = new_q.scalar() or 0

    # Top customers
    top_q = await db.execute(
        select(User.first_name, User.last_name, func.count(Booking.id).label("bookings"), func.sum(Booking.total_amount).label("rev"))
        .join(Booking, Booking.user_id == User.id)
        .where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(d_from, d_to))
        .group_by(User.id, User.first_name, User.last_name)
        .order_by(func.sum(Booking.total_amount).desc()).limit(10)
    )
    top = [{"name": f"{r[0]} {r[1]}", "bookings": r[2], "revenue": float(r[3] or 0)} for r in top_q.all()]

    return {
        "total_customers": total_customers, "new_customers_period": new_customers, "top_customers": top,
    }


@router.get("/satisfaction", dependencies=[AdminUser])
async def satisfaction_analytics(db: DBSession, date_from: str | None = None, date_to: str | None = None):
    d_from, d_to = _parse_dates(date_from, date_to)

    avg_q = await db.execute(
        select(func.avg(TripReview.overall_rating), func.count(TripReview.id))
        .where(func.date(TripReview.created_at).between(d_from, d_to), TripReview.is_visible == True)  # noqa: E712
    )
    avg_row = avg_q.one()

    dist_q = await db.execute(
        select(TripReview.overall_rating, func.count(TripReview.id))
        .where(func.date(TripReview.created_at).between(d_from, d_to), TripReview.is_visible == True)  # noqa: E712
        .group_by(TripReview.overall_rating).order_by(TripReview.overall_rating.desc())
    )
    distribution = [{"stars": r[0], "count": r[1]} for r in dist_q.all()]

    return {
        "avg_overall_rating": round(float(avg_row[0] or 0), 1),
        "total_reviews": avg_row[1] or 0,
        "rating_distribution": distribution,
    }


# ── Dashboard Widgets ──

@router.get("/dashboard/widgets")
async def list_widgets(db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(DashboardWidget).where(DashboardWidget.user_id == current_user.id, DashboardWidget.is_visible == True)  # noqa: E712
        .order_by(DashboardWidget.created_at)
    )
    return [
        {"id": str(w.id), "widget_type": w.widget_type, "data_source": w.data_source, "title": w.title, "config": w.config, "position": w.position}
        for w in result.scalars().all()
    ]


from pydantic import BaseModel, Field


class CreateWidgetRequest(BaseModel):
    widget_type: str
    data_source: str
    title: str = Field(..., max_length=200)
    config: dict | None = None
    position: dict | None = None


@router.post("/dashboard/widgets", status_code=201)
async def create_widget(data: CreateWidgetRequest, db: DBSession, current_user: CurrentUser):
    widget = DashboardWidget(user_id=current_user.id, **data.model_dump())
    db.add(widget)
    await db.flush()
    return {"id": str(widget.id)}


@router.delete("/dashboard/widgets/{widget_id}")
async def delete_widget(widget_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    from app.core.exceptions import NotFoundError
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id, DashboardWidget.user_id == current_user.id))
    w = result.scalar_one_or_none()
    if not w:
        raise NotFoundError("Widget not found")
    await db.delete(w)
    await db.flush()
    return {"deleted": True}
