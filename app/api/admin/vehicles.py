import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole, VehicleStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.vehicle import Vehicle, VehicleType

router = APIRouter(prefix="/vehicles", tags=["Admin - Vehicles"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER))


class CreateVehicleTypeRequest(BaseModel):
    name: str = Field(..., max_length=100)
    description: str | None = None
    seat_capacity: int
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
