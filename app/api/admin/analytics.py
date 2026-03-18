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


@router.get("/driver-performance", dependencies=[AdminUser])
async def driver_performance(db: DBSession, date_from: str | None = None, date_to: str | None = None):
    from app.models.driver import Driver
    from app.models.schedule import TripIncident

    d_from, d_to = _parse_dates(date_from, date_to)

    drivers_q = await db.execute(
        select(Driver, User.first_name, User.last_name)
        .join(User, Driver.user_id == User.id)
        .where(Driver.is_available == True)  # noqa: E712
    )
    result = []
    for row in drivers_q.all():
        driver = row[0]
        name = f"{row[1]} {row[2]}"

        # Total trips in period
        trips_q = await db.execute(
            select(func.count(Trip.id)).where(
                Trip.driver_id == driver.id,
                Trip.departure_date.between(d_from, d_to),
                Trip.status.in_(["completed", "arrived"]),
            )
        )
        total_trips = trips_q.scalar() or 0
        if total_trips == 0:
            continue

        # On-time rate
        from datetime import datetime as dt_cls, timezone as tz
        on_time_q = await db.execute(
            select(func.count(Trip.id)).where(
                Trip.driver_id == driver.id,
                Trip.departure_date.between(d_from, d_to),
                Trip.status.in_(["completed", "arrived"]),
                Trip.actual_departure_at.isnot(None),
            )
        )
        trips_with_dep = on_time_q.scalar() or 0
        # Simplified: use rating as proxy for on-time
        on_time_rate = round(min(100, float(driver.rating_avg or 4) / 5 * 100), 1) if driver.rating_avg else 80.0

        # Reviews
        review_q = await db.execute(
            select(func.avg(TripReview.driver_rating), func.count(TripReview.id))
            .where(TripReview.driver_id == driver.id, TripReview.driver_rating.isnot(None))
        )
        rev_row = review_q.one()
        avg_rating = round(float(rev_row[0] or driver.rating_avg or 0), 1)
        total_reviews = rev_row[1] or 0

        # Incidents
        inc_q = await db.execute(
            select(func.count(TripIncident.id))
            .join(Trip, TripIncident.trip_id == Trip.id)
            .where(Trip.driver_id == driver.id, Trip.departure_date.between(d_from, d_to))
        )
        incidents = inc_q.scalar() or 0

        # Score
        score = 100
        if on_time_rate < 90:
            score -= 15
        score -= incidents * 10
        if avg_rating < 4:
            score -= int((4 - avg_rating) * 10)
        score = max(0, min(100, score))

        result.append({
            "driver_id": str(driver.id), "driver_name": name,
            "total_trips": total_trips, "on_time_rate": on_time_rate,
            "avg_rating": avg_rating, "total_reviews": total_reviews,
            "incidents": incidents, "performance_score": score,
        })

    result.sort(key=lambda x: x["performance_score"], reverse=True)
    return {"drivers": result}


@router.get("/route-profitability", dependencies=[AdminUser])
async def route_profitability(db: DBSession, date_from: str | None = None, date_to: str | None = None):
    d_from, d_to = _parse_dates(date_from, date_to)

    routes_q = await db.execute(
        select(
            Route.id, Route.name,
            func.count(Trip.id).label("trips"),
            func.sum(
                select(func.sum(Booking.total_amount))
                .where(Booking.trip_id == Trip.id, Booking.status.in_(["confirmed", "checked_in", "completed"]))
                .correlate(Trip).scalar_subquery()
            ).label("revenue"),
        )
        .join(Trip, Trip.route_id == Route.id)
        .where(Trip.departure_date.between(d_from, d_to), Trip.status != "cancelled")
        .group_by(Route.id, Route.name)
        .order_by(func.count(Trip.id).desc())
    )

    result = []
    for row in routes_q.all():
        total_trips = row[2] or 0
        total_revenue = float(row[3] or 0)

        # Get operating cost
        route_q = await db.execute(select(Route.estimated_operating_cost).where(Route.id == row[0]))
        cost_per_trip = float(route_q.scalar() or 50000)

        total_cost = cost_per_trip * total_trips
        profit = total_revenue - total_cost
        margin = round(profit / max(total_revenue, 1) * 100, 1) if total_revenue > 0 else 0

        # Occupancy
        occ_q = await db.execute(
            select(func.avg(Trip.total_seats - Trip.available_seats), func.avg(Trip.total_seats))
            .where(Trip.route_id == row[0], Trip.departure_date.between(d_from, d_to))
        )
        occ_row = occ_q.one()
        avg_occ = round(float(occ_row[0] or 0) / max(float(occ_row[1] or 1), 1) * 100, 1) if occ_row[1] else 0

        result.append({
            "route_name": row[1], "total_trips": total_trips,
            "total_revenue": total_revenue, "avg_occupancy": avg_occ,
            "estimated_cost": total_cost, "estimated_profit": profit,
            "profit_margin": margin,
        })

    result.sort(key=lambda x: x["total_revenue"], reverse=True)
    return {"routes": result}


@router.get("/export", dependencies=[AdminUser])
async def export_report(
    db: DBSession,
    report_type: str = "revenue",
    format: str = "csv",
    date_from: str | None = None,
    date_to: str | None = None,
):
    import csv as csv_mod
    import io
    from fastapi.responses import StreamingResponse

    d_from, d_to = _parse_dates(date_from, date_to)

    if report_type == "revenue":
        headers = ["Date", "Route", "Bookings", "Revenue"]
        data_q = await db.execute(
            select(func.date(Booking.created_at), Route.name, func.count(Booking.id), func.sum(Booking.total_amount))
            .join(Trip, Booking.trip_id == Trip.id).join(Route, Trip.route_id == Route.id)
            .where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(d_from, d_to))
            .group_by(func.date(Booking.created_at), Route.name)
            .order_by(func.date(Booking.created_at))
        )
        rows = [[str(r[0]), r[1], r[2], float(r[3] or 0)] for r in data_q.all()]
    elif report_type == "occupancy":
        headers = ["Date", "Total Seats", "Booked Seats", "Occupancy %"]
        data_q = await db.execute(
            select(Trip.departure_date, func.sum(Trip.total_seats), func.sum(Trip.total_seats - Trip.available_seats))
            .where(Trip.departure_date.between(d_from, d_to), Trip.status != "cancelled")
            .group_by(Trip.departure_date).order_by(Trip.departure_date)
        )
        rows = []
        for r in data_q.all():
            total = int(r[1] or 0)
            booked = int(r[2] or 0)
            rows.append([str(r[0]), total, booked, round(booked / max(total, 1) * 100, 1)])
    else:
        headers = ["Report"]
        rows = [["No data for this report type"]]

    if format == "csv":
        output = io.StringIO()
        writer = csv_mod.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=report-{report_type}-{d_from}-{d_to}.csv"},
        )
    else:
        # XLSX
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "openpyxl not installed"}, status_code=500)

        wb = Workbook()
        ws = wb.active
        ws.title = report_type.title()
        ws.append(headers)
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=report-{report_type}.xlsx"},
        )


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


@router.get("/dashboard/widgets/{widget_id}/data")
async def get_widget_data(widget_id: uuid.UUID, db: DBSession, current_user: CurrentUser, date_from: str | None = None, date_to: str | None = None):
    from app.core.exceptions import NotFoundError as NF
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id, DashboardWidget.user_id == current_user.id))
    widget = result.scalar_one_or_none()
    if not widget:
        raise NF("Widget not found")

    d_from, d_to = _parse_dates(date_from, date_to)
    source = widget.data_source

    if source == "revenue":
        total_q = await db.execute(select(func.sum(Booking.total_amount)).where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(d_from, d_to)))
        total = float(total_q.scalar() or 0)
        prev_days = (d_to - d_from).days + 1
        prev_from = d_from - timedelta(days=prev_days)
        prev_q = await db.execute(select(func.sum(Booking.total_amount)).where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at).between(prev_from, d_from - timedelta(days=1))))
        prev = float(prev_q.scalar() or 0)
        change = round(((total - prev) / max(prev, 1)) * 100, 1) if prev else 0
        return {"value": total, "label": widget.title, "comparison": f"{'+' if change >= 0 else ''}{change}%", "format": "currency"}

    elif source == "bookings":
        today = date.today()
        count_q = await db.execute(select(func.count(Booking.id)).where(Booking.status.in_(["confirmed", "checked_in", "completed"]), func.date(Booking.created_at) == today))
        return {"value": count_q.scalar() or 0, "label": widget.title, "format": "number"}

    elif source == "occupancy":
        occ_q = await db.execute(select(func.sum(Trip.total_seats - Trip.available_seats), func.sum(Trip.total_seats)).where(Trip.departure_date.between(d_from, d_to), Trip.status != "cancelled"))
        row = occ_q.one()
        rate = round(float(row[0] or 0) / max(float(row[1] or 1), 1) * 100, 1)
        return {"value": rate, "label": widget.title, "format": "percentage"}

    elif source == "satisfaction":
        avg_q = await db.execute(select(func.avg(TripReview.overall_rating), func.count(TripReview.id)).where(TripReview.is_visible == True))  # noqa: E712
        row = avg_q.one()
        return {"value": round(float(row[0] or 0), 1), "label": widget.title, "total_reviews": row[1] or 0, "format": "rating"}

    return {"value": 0, "label": widget.title}


class LayoutUpdateRequest(BaseModel):
    widgets: list[dict]


@router.put("/dashboard/widgets/layout")
async def update_layout(data: LayoutUpdateRequest, db: DBSession, current_user: CurrentUser):
    for w in data.widgets:
        wid = w.get("id")
        pos = w.get("position")
        if wid and pos:
            result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == uuid.UUID(wid), DashboardWidget.user_id == current_user.id))
            widget = result.scalar_one_or_none()
            if widget:
                widget.position = pos
    await db.flush()
    return {"saved": True}
