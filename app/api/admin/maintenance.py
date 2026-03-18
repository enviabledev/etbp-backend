import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.maintenance import MaintenanceRecord, MaintenanceSchedule, VehicleDocument
from app.models.vehicle import Vehicle
from app.services.audit_service import log_action

router = APIRouter(prefix="/maintenance", tags=["Admin - Maintenance"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateMaintenanceRequest(BaseModel):
    vehicle_id: uuid.UUID
    maintenance_type: str
    title: str = Field(..., max_length=200)
    description: str | None = None
    scheduled_date: date
    priority: str = "medium"
    vendor_name: str | None = None
    vendor_contact: str | None = None
    cost: float | None = None


class UpdateStatusRequest(BaseModel):
    status: str
    notes: str | None = None
    cost: float | None = None
    mileage_at_service: float | None = None
    parts_replaced: list | None = None
    next_service_due_date: date | None = None
    next_service_due_mileage: float | None = None


class CreateScheduleRequest(BaseModel):
    vehicle_type_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    maintenance_type: str
    title: str = Field(..., max_length=200)
    interval_km: int | None = None
    interval_days: int | None = None


class CreateDocumentRequest(BaseModel):
    document_type: str
    document_number: str | None = None
    issued_date: date | None = None
    expiry_date: date
    notes: str | None = None


# ── Maintenance Records ──

@router.get("", dependencies=[AdminUser])
async def list_maintenance(
    db: DBSession,
    vehicle_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(MaintenanceRecord)
    if vehicle_id:
        query = query.where(MaintenanceRecord.vehicle_id == vehicle_id)
    if status:
        query = query.where(MaintenanceRecord.status == status)

    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0

    query = query.order_by(MaintenanceRecord.scheduled_date.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for r in result.scalars().all():
        # Get vehicle plate
        v_q = await db.execute(select(Vehicle.plate_number).where(Vehicle.id == r.vehicle_id))
        plate = v_q.scalar()
        items.append({
            "id": str(r.id), "vehicle_id": str(r.vehicle_id), "vehicle_plate": plate,
            "maintenance_type": r.maintenance_type, "title": r.title,
            "status": r.status, "priority": r.priority,
            "scheduled_date": str(r.scheduled_date),
            "cost": float(r.cost) if r.cost else None,
            "created_at": str(r.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/stats", dependencies=[AdminUser])
async def maintenance_stats(db: DBSession):
    today = date.today()
    week_ahead = today + timedelta(days=7)
    month_start = today.replace(day=1)

    upcoming_q = await db.execute(select(func.count(MaintenanceRecord.id)).where(
        MaintenanceRecord.status == "scheduled", MaintenanceRecord.scheduled_date <= week_ahead, MaintenanceRecord.scheduled_date >= today))
    overdue_q = await db.execute(select(func.count(MaintenanceRecord.id)).where(
        MaintenanceRecord.status.in_(["scheduled", "overdue"]), MaintenanceRecord.scheduled_date < today))
    in_progress_q = await db.execute(select(func.count(MaintenanceRecord.id)).where(MaintenanceRecord.status == "in_progress"))
    completed_q = await db.execute(select(func.count(MaintenanceRecord.id)).where(
        MaintenanceRecord.status == "completed", MaintenanceRecord.completed_at >= month_start))
    cost_q = await db.execute(select(func.sum(MaintenanceRecord.cost)).where(
        MaintenanceRecord.status == "completed", MaintenanceRecord.completed_at >= month_start))
    docs_exp_q = await db.execute(select(func.count(VehicleDocument.id)).where(
        VehicleDocument.expiry_date <= today + timedelta(days=30), VehicleDocument.status != "expired"))
    docs_expired_q = await db.execute(select(func.count(VehicleDocument.id)).where(VehicleDocument.status == "expired"))

    return {
        "upcoming_7_days": upcoming_q.scalar() or 0,
        "overdue": overdue_q.scalar() or 0,
        "in_progress": in_progress_q.scalar() or 0,
        "completed_this_month": completed_q.scalar() or 0,
        "total_cost_this_month": float(cost_q.scalar() or 0),
        "documents_expiring_30_days": docs_exp_q.scalar() or 0,
        "documents_expired": docs_expired_q.scalar() or 0,
    }


@router.get("/upcoming", dependencies=[AdminUser])
async def upcoming_maintenance(db: DBSession, days_ahead: int = Query(30, ge=1, le=365)):
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = await db.execute(
        select(MaintenanceRecord).where(
            MaintenanceRecord.status.in_(["scheduled", "overdue"]),
            MaintenanceRecord.scheduled_date <= cutoff,
        ).order_by(MaintenanceRecord.scheduled_date.asc())
    )
    items = []
    for r in result.scalars().all():
        v_q = await db.execute(select(Vehicle.plate_number).where(Vehicle.id == r.vehicle_id))
        items.append({
            "id": str(r.id), "vehicle_plate": v_q.scalar(),
            "title": r.title, "maintenance_type": r.maintenance_type,
            "priority": r.priority, "status": r.status,
            "scheduled_date": str(r.scheduled_date),
            "is_overdue": r.scheduled_date < today,
        })
    return {"items": items}


@router.get("/{record_id}", dependencies=[AdminUser])
async def get_maintenance(record_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    r = result.scalar_one_or_none()
    if not r:
        raise NotFoundError("Maintenance record not found")
    v_q = await db.execute(select(Vehicle.plate_number).where(Vehicle.id == r.vehicle_id))
    return {
        "id": str(r.id), "vehicle_id": str(r.vehicle_id), "vehicle_plate": v_q.scalar(),
        "maintenance_type": r.maintenance_type, "title": r.title, "description": r.description,
        "status": r.status, "priority": r.priority, "scheduled_date": str(r.scheduled_date),
        "started_at": str(r.started_at) if r.started_at else None,
        "completed_at": str(r.completed_at) if r.completed_at else None,
        "mileage_at_service": float(r.mileage_at_service) if r.mileage_at_service else None,
        "cost": float(r.cost) if r.cost else None, "currency": r.currency,
        "vendor_name": r.vendor_name, "vendor_contact": r.vendor_contact,
        "parts_replaced": r.parts_replaced, "notes": r.notes,
        "next_service_due_date": str(r.next_service_due_date) if r.next_service_due_date else None,
        "next_service_due_mileage": float(r.next_service_due_mileage) if r.next_service_due_mileage else None,
        "created_at": str(r.created_at),
    }


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_maintenance(data: CreateMaintenanceRequest, db: DBSession, current_user: CurrentUser):
    record = MaintenanceRecord(
        vehicle_id=data.vehicle_id, maintenance_type=data.maintenance_type,
        title=data.title, description=data.description,
        scheduled_date=data.scheduled_date, priority=data.priority,
        vendor_name=data.vendor_name, vendor_contact=data.vendor_contact,
        cost=data.cost, created_by=current_user.id,
    )
    db.add(record)
    await db.flush()
    await log_action(db, current_user.id, "create_maintenance", "maintenance", str(record.id), {"vehicle_id": str(data.vehicle_id), "title": data.title})
    return {"id": str(record.id), "status": record.status}


@router.patch("/{record_id}/status", dependencies=[AdminUser])
async def update_maintenance_status(record_id: uuid.UUID, data: UpdateStatusRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(MaintenanceRecord).where(MaintenanceRecord.id == record_id))
    r = result.scalar_one_or_none()
    if not r:
        raise NotFoundError("Maintenance record not found")

    r.status = data.status
    if data.notes:
        r.notes = (r.notes or "") + f"\n[{data.status}] {data.notes}"
    if data.status == "in_progress":
        r.started_at = datetime.now(timezone.utc)
    elif data.status == "completed":
        r.completed_at = datetime.now(timezone.utc)
        if data.cost is not None:
            r.cost = data.cost
        if data.mileage_at_service is not None:
            r.mileage_at_service = data.mileage_at_service
        if data.parts_replaced is not None:
            r.parts_replaced = data.parts_replaced
        if data.next_service_due_date:
            r.next_service_due_date = data.next_service_due_date
        if data.next_service_due_mileage is not None:
            r.next_service_due_mileage = data.next_service_due_mileage

    await db.flush()
    await log_action(db, current_user.id, "update_maintenance_status", "maintenance", str(record_id), {"status": data.status})
    return {"id": str(r.id), "status": r.status}


# ── Maintenance Schedules ──

@router.get("/schedules", dependencies=[AdminUser])
async def list_schedules(db: DBSession):
    result = await db.execute(select(MaintenanceSchedule).order_by(MaintenanceSchedule.created_at.desc()))
    return [
        {
            "id": str(s.id), "maintenance_type": s.maintenance_type, "title": s.title,
            "vehicle_type_id": str(s.vehicle_type_id) if s.vehicle_type_id else None,
            "vehicle_id": str(s.vehicle_id) if s.vehicle_id else None,
            "interval_km": s.interval_km, "interval_days": s.interval_days,
            "is_active": s.is_active,
        }
        for s in result.scalars().all()
    ]


@router.post("/schedules", status_code=201, dependencies=[AdminUser])
async def create_schedule(data: CreateScheduleRequest, db: DBSession, current_user: CurrentUser):
    sched = MaintenanceSchedule(**data.model_dump(), created_by=current_user.id)
    db.add(sched)
    await db.flush()
    return {"id": str(sched.id)}


# ── Vehicle Documents ──

@router.get("/vehicles/{vehicle_id}/documents", dependencies=[AdminUser])
async def list_vehicle_documents(vehicle_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(VehicleDocument).where(VehicleDocument.vehicle_id == vehicle_id)
        .order_by(VehicleDocument.expiry_date.asc())
    )
    return [
        {
            "id": str(d.id), "document_type": d.document_type,
            "document_number": d.document_number,
            "issued_date": str(d.issued_date) if d.issued_date else None,
            "expiry_date": str(d.expiry_date), "status": d.status,
            "notes": d.notes,
        }
        for d in result.scalars().all()
    ]


@router.post("/vehicles/{vehicle_id}/documents", status_code=201, dependencies=[AdminUser])
async def create_vehicle_document(vehicle_id: uuid.UUID, data: CreateDocumentRequest, db: DBSession, current_user: CurrentUser):
    doc = VehicleDocument(vehicle_id=vehicle_id, **data.model_dump(), created_by=current_user.id)
    db.add(doc)
    await db.flush()
    return {"id": str(doc.id), "status": doc.status}


@router.get("/vehicle-documents/expiring", dependencies=[AdminUser])
async def expiring_documents(db: DBSession, days_ahead: int = Query(30, ge=1, le=365)):
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = await db.execute(
        select(VehicleDocument).where(
            VehicleDocument.expiry_date <= cutoff,
        ).order_by(VehicleDocument.expiry_date.asc())
    )
    items = []
    for d in result.scalars().all():
        v_q = await db.execute(select(Vehicle.plate_number).where(Vehicle.id == d.vehicle_id))
        items.append({
            "id": str(d.id), "vehicle_plate": v_q.scalar(),
            "document_type": d.document_type, "expiry_date": str(d.expiry_date),
            "status": d.status, "is_expired": d.expiry_date < today,
            "days_until_expiry": (d.expiry_date - today).days,
        })
    return {"items": items}
