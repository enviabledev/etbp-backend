import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole, VehicleStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.driver import Driver
from app.models.route import Route
from app.models.schedule import Trip
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleType

router = APIRouter(prefix="/vehicles", tags=["Admin - Vehicles"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER))


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateVehicleTypeRequest(BaseModel):
    name: str = Field(..., max_length=100)
    description: str | None = None
    seat_capacity: int
    seat_layout: dict | None = None
    amenities: dict | None = None


class UpdateVehicleTypeRequest(BaseModel):
    name: str | None = Field(None, max_length=100)
    description: str | None = None
    seat_capacity: int | None = None
    seat_layout: dict | None = None
    amenities: dict | None = None


class CreateVehicleRequest(BaseModel):
    vehicle_type_id: uuid.UUID
    plate_number: str = Field(..., max_length=20)
    make: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None


class UpdateVehicleRequest(BaseModel):
    make: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None
    status: VehicleStatus | None = None
    current_mileage: float | None = None
    last_service_date: date | None = None
    next_service_due: date | None = None
    insurance_expiry: date | None = None
    registration_expiry: date | None = None
    inspection_expiry: date | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Vehicle type endpoints (BEFORE /{vehicle_id} to avoid path conflicts)
# ---------------------------------------------------------------------------

@router.post("/types", status_code=201, dependencies=[AdminUser])
async def create_vehicle_type(data: CreateVehicleTypeRequest, db: DBSession):
    vt = VehicleType(**data.model_dump())
    db.add(vt)
    await db.flush()
    await db.refresh(vt)
    return vt


@router.get("/types", dependencies=[AdminUser])
async def list_vehicle_types(db: DBSession):
    result = await db.execute(select(VehicleType).order_by(VehicleType.name))
    return result.scalars().all()


@router.get("/types/{type_id}", dependencies=[AdminUser])
async def get_vehicle_type_detail(type_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(VehicleType)
        .options(selectinload(VehicleType.vehicles))
        .where(VehicleType.id == type_id)
    )
    vt = result.scalar_one_or_none()
    if not vt:
        raise NotFoundError("Vehicle type not found")

    active_count = sum(1 for v in vt.vehicles if v.status == "active")

    return {
        "vehicle_type": vt,
        "vehicles": vt.vehicles,
        "active_vehicle_count": active_count,
    }


@router.put("/types/{type_id}", dependencies=[AdminUser])
async def update_vehicle_type(type_id: uuid.UUID, data: UpdateVehicleTypeRequest, db: DBSession):
    result = await db.execute(select(VehicleType).where(VehicleType.id == type_id))
    vt = result.scalar_one_or_none()
    if not vt:
        raise NotFoundError("Vehicle type not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vt, field, value)

    await db.flush()
    await db.refresh(vt)
    return vt


@router.delete("/types/{type_id}", dependencies=[AdminUser])
async def delete_vehicle_type(type_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(VehicleType).where(VehicleType.id == type_id))
    vt = result.scalar_one_or_none()
    if not vt:
        raise NotFoundError("Vehicle type not found")

    # Check if any vehicles reference this type
    vehicle_count_q = await db.execute(
        select(func.count(Vehicle.id)).where(Vehicle.vehicle_type_id == type_id)
    )
    count = vehicle_count_q.scalar() or 0
    if count > 0:
        raise ConflictError(
            f"Cannot delete vehicle type: {count} vehicle(s) still reference this type"
        )

    await db.delete(vt)
    await db.flush()
    return {"message": "Vehicle type deleted"}


# ---------------------------------------------------------------------------
# Vehicle CRUD endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201, dependencies=[AdminUser])
async def create_vehicle(data: CreateVehicleRequest, db: DBSession):
    existing = await db.execute(
        select(Vehicle).where(Vehicle.plate_number == data.plate_number.upper())
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Vehicle with this plate number already exists")

    vehicle = Vehicle(**data.model_dump())
    vehicle.plate_number = vehicle.plate_number.upper()
    db.add(vehicle)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


@router.get("", dependencies=[AdminUser])
async def list_vehicles(
    db: DBSession,
    status: VehicleStatus | None = None,
    vehicle_type_id: uuid.UUID | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Vehicle).options(selectinload(Vehicle.vehicle_type))
    if status:
        query = query.where(Vehicle.status == status.value)
    if vehicle_type_id:
        query = query.where(Vehicle.vehicle_type_id == vehicle_type_id)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Vehicle.plate_number.ilike(pattern)
            | Vehicle.make.ilike(pattern)
            | Vehicle.model.ilike(pattern)
        )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(Vehicle.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/{vehicle_id}/detail", dependencies=[AdminUser])
async def get_vehicle_detail(vehicle_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Vehicle)
        .options(selectinload(Vehicle.vehicle_type))
        .where(Vehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")

    today = date.today()

    def _doc_info(expiry_date: date | None) -> dict:
        if expiry_date is None:
            return {"expiry": None, "days_remaining": None, "is_expiring_soon": False}
        days = (expiry_date - today).days
        return {
            "expiry": str(expiry_date),
            "days_remaining": days,
            "is_expiring_soon": days <= 30,
        }

    # Maintenance info
    is_service_overdue = False
    if vehicle.next_service_due:
        is_service_overdue = vehicle.next_service_due < today

    # Trip history: last 20 trips with route name, date, driver name, status
    trip_q = await db.execute(
        select(
            Trip.id,
            Route.name.label("route_name"),
            Trip.departure_date,
            Trip.status,
            User.first_name.label("driver_first_name"),
            User.last_name.label("driver_last_name"),
        )
        .join(Route, Route.id == Trip.route_id)
        .outerjoin(Driver, Driver.id == Trip.driver_id)
        .outerjoin(User, User.id == Driver.user_id)
        .where(Trip.vehicle_id == vehicle_id)
        .order_by(Trip.departure_date.desc(), Trip.departure_time.desc())
        .limit(20)
    )
    trip_history = [
        {
            "id": str(row.id),
            "route_name": row.route_name,
            "departure_date": str(row.departure_date),
            "driver_name": (
                f"{row.driver_first_name} {row.driver_last_name}"
                if row.driver_first_name
                else None
            ),
            "status": row.status,
        }
        for row in trip_q.all()
    ]

    return {
        "vehicle": vehicle,
        "documents": {
            "insurance": _doc_info(vehicle.insurance_expiry),
            "registration": _doc_info(vehicle.registration_expiry),
            "inspection": _doc_info(vehicle.inspection_expiry),
        },
        "maintenance": {
            "last_service_date": str(vehicle.last_service_date) if vehicle.last_service_date else None,
            "next_service_due": str(vehicle.next_service_due) if vehicle.next_service_due else None,
            "is_service_overdue": is_service_overdue,
        },
        "trip_history": trip_history,
        "notes": vehicle.notes,
    }


@router.get("/{vehicle_id}", dependencies=[AdminUser])
async def get_vehicle(vehicle_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Vehicle)
        .options(selectinload(Vehicle.vehicle_type))
        .where(Vehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    return vehicle


@router.put("/{vehicle_id}", dependencies=[AdminUser])
async def update_vehicle(vehicle_id: uuid.UUID, data: UpdateVehicleRequest, db: DBSession):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        if isinstance(value, VehicleStatus):
            value = value.value
        setattr(vehicle, field, value)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", dependencies=[AdminUser])
async def delete_vehicle(vehicle_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")

    vehicle.status = "retired"
    await db.flush()
    return {"message": "Vehicle retired"}
