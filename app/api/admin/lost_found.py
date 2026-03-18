import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.lost_found import LostFoundReport
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/lost-found", tags=["Admin - Lost & Found"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class UpdateStatusRequest(BaseModel):
    status: str
    notes: str | None = None
    assigned_to: uuid.UUID | None = None


@router.get("", dependencies=[AdminUser])
async def list_reports(db: DBSession, status: str | None = None, report_type: str | None = None, category: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(LostFoundReport)
    if status:
        query = query.where(LostFoundReport.status == status)
    if report_type:
        query = query.where(LostFoundReport.report_type == report_type)
    if category:
        query = query.where(LostFoundReport.item_category == category)
    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0
    query = query.order_by(LostFoundReport.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for r in result.scalars().all():
        user_q = await db.execute(select(User.first_name, User.last_name).where(User.id == r.reporter_user_id))
        user_row = user_q.one_or_none()
        items.append({
            "id": str(r.id), "report_number": r.report_number, "report_type": r.report_type,
            "item_category": r.item_category, "item_description": (r.item_description or "")[:80],
            "reporter": f"{user_row[0]} {user_row[1]}" if user_row else "Unknown",
            "date_lost_found": str(r.date_lost_found), "status": r.status,
            "created_at": str(r.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/stats", dependencies=[AdminUser])
async def report_stats(db: DBSession):
    open_q = await db.execute(select(func.count(LostFoundReport.id)).where(LostFoundReport.status.in_(["reported", "investigating"])))
    found_q = await db.execute(select(func.count(LostFoundReport.id)).where(LostFoundReport.status == "found"))
    returned_q = await db.execute(select(func.count(LostFoundReport.id)).where(LostFoundReport.status == "returned"))
    return {"open": open_q.scalar() or 0, "found": found_q.scalar() or 0, "returned": returned_q.scalar() or 0}


@router.get("/{report_id}", dependencies=[AdminUser])
async def get_report(report_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(LostFoundReport).where(LostFoundReport.id == report_id))
    r = result.scalar_one_or_none()
    if not r:
        raise NotFoundError("Report not found")
    user_q = await db.execute(select(User.first_name, User.last_name, User.email, User.phone).where(User.id == r.reporter_user_id))
    user_row = user_q.one_or_none()
    return {
        "id": str(r.id), "report_number": r.report_number, "report_type": r.report_type,
        "item_description": r.item_description, "item_category": r.item_category,
        "color": r.color, "distinguishing_features": r.distinguishing_features,
        "date_lost_found": str(r.date_lost_found), "location_details": r.location_details,
        "booking_ref": r.booking_ref, "status": r.status,
        "resolution_notes": r.resolution_notes,
        "reporter": {"name": f"{user_row[0]} {user_row[1]}" if user_row else "", "email": user_row[2] if user_row else "", "phone": user_row[3] if user_row else ""},
        "contact_phone": r.contact_phone, "contact_email": r.contact_email,
        "assigned_to": str(r.assigned_to) if r.assigned_to else None,
        "images": r.images, "created_at": str(r.created_at),
    }


@router.patch("/{report_id}/status", dependencies=[AdminUser])
async def update_status(report_id: uuid.UUID, data: UpdateStatusRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(LostFoundReport).where(LostFoundReport.id == report_id))
    r = result.scalar_one_or_none()
    if not r:
        raise NotFoundError("Report not found")
    r.status = data.status
    if data.notes:
        r.resolution_notes = (r.resolution_notes or "") + f"\n[{data.status}] {data.notes}"
    if data.assigned_to:
        r.assigned_to = data.assigned_to
    if data.status in ("returned", "closed"):
        r.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    await log_action(db, current_user.id, "update_lost_found_status", "lost_found", str(report_id), {"status": data.status})

    # Notify reporter
    try:
        from app.services.push_notification_service import send_push_to_user
        messages = {
            "investigating": "We're looking into your lost item report.",
            "found": f"Great news! Your {r.item_category} has been found. Contact us to arrange pickup.",
            "returned": "Your item has been returned.",
            "closed": "Your lost item report has been closed.",
        }
        msg = messages.get(data.status)
        if msg:
            await send_push_to_user(db, r.reporter_user_id, "Lost & Found Update", msg, {"type": "lost_found_update", "report_number": r.report_number}, "customer")
    except Exception:
        pass

    return {"id": str(r.id), "status": r.status}
