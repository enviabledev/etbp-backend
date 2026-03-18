import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.lost_found import LostFoundReport

router = APIRouter(prefix="/lost-found", tags=["Lost & Found"])


class CreateReportRequest(BaseModel):
    report_type: str  # "lost" | "found"
    item_description: str
    item_category: str
    color: str | None = None
    distinguishing_features: str | None = None
    date_lost_found: str  # date string
    location_details: str | None = None
    booking_ref: str | None = None
    contact_phone: str
    contact_email: str | None = None


@router.post("", status_code=201)
async def create_report(data: CreateReportRequest, db: DBSession, current_user: CurrentUser):
    from datetime import date as date_cls

    # Generate report number
    year = datetime.now().year
    count_q = await db.execute(select(func.count(LostFoundReport.id)).where(func.extract("year", LostFoundReport.created_at) == year))
    seq = (count_q.scalar() or 0) + 1
    report_number = f"LF-{year}-{seq:04d}"

    # Link booking if provided
    trip_id = None
    route_id = None
    if data.booking_ref:
        from app.models.booking import Booking
        booking_q = await db.execute(select(Booking.trip_id).where(Booking.reference == data.booking_ref.upper()))
        tid = booking_q.scalar_one_or_none()
        if tid:
            trip_id = tid
            from app.models.schedule import Trip
            trip_q = await db.execute(select(Trip.route_id).where(Trip.id == tid))
            route_id = trip_q.scalar_one_or_none()

    report = LostFoundReport(
        report_number=report_number,
        reporter_user_id=current_user.id,
        report_type=data.report_type,
        booking_ref=data.booking_ref,
        trip_id=trip_id,
        route_id=route_id,
        item_description=data.item_description,
        item_category=data.item_category,
        color=data.color,
        distinguishing_features=data.distinguishing_features,
        date_lost_found=date_cls.fromisoformat(data.date_lost_found),
        location_details=data.location_details,
        contact_phone=data.contact_phone,
        contact_email=data.contact_email,
    )
    db.add(report)
    await db.flush()

    # Notify admin
    try:
        from app.services.push_notification_service import send_push_to_multiple
        from app.models.device_token import DeviceToken
        admin_tokens_q = await db.execute(select(DeviceToken.token).where(DeviceToken.app_type == "admin", DeviceToken.is_active == True))  # noqa: E712
        tokens = list(admin_tokens_q.scalars().all())
        if tokens:
            await send_push_to_multiple(tokens, "Lost Item Report", f"New {data.report_type} report: {data.item_category} — {data.item_description[:50]}", {"type": "lost_found_report"})
    except Exception:
        pass

    return {"report_number": report_number, "id": str(report.id), "status": report.status}


@router.get("")
async def my_reports(db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(LostFoundReport).where(LostFoundReport.reporter_user_id == current_user.id)
        .order_by(LostFoundReport.created_at.desc())
    )
    return [
        {
            "id": str(r.id), "report_number": r.report_number, "report_type": r.report_type,
            "item_category": r.item_category, "item_description": r.item_description[:100],
            "status": r.status, "date_lost_found": str(r.date_lost_found), "created_at": str(r.created_at),
        }
        for r in result.scalars().all()
    ]


@router.get("/{report_number}")
async def get_report(report_number: str, db: DBSession, current_user: CurrentUser):
    result = await db.execute(
        select(LostFoundReport).where(LostFoundReport.report_number == report_number.upper(), LostFoundReport.reporter_user_id == current_user.id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise NotFoundError("Report not found")
    return {
        "id": str(r.id), "report_number": r.report_number, "report_type": r.report_type,
        "item_description": r.item_description, "item_category": r.item_category,
        "color": r.color, "distinguishing_features": r.distinguishing_features,
        "date_lost_found": str(r.date_lost_found), "location_details": r.location_details,
        "booking_ref": r.booking_ref, "contact_phone": r.contact_phone, "contact_email": r.contact_email,
        "status": r.status, "resolution_notes": r.resolution_notes,
        "created_at": str(r.created_at),
    }
