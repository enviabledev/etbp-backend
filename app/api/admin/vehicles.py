import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole, VehicleStatus
from app.core.exceptions import NotFoundError
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
    vehicle = Vehicle(**data.model_dump())
    db.add(vehicle)
    await db.flush()
    await db.refresh(vehicle)
    return vehicle


@router.get("", dependencies=[AdminUser])
async def list_vehicles(
    db: DBSession,
    status: VehicleStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Vehicle).options(selectinload(Vehicle.vehicle_type))
    if status:
        query = query.where(Vehicle.status == status.value)
    query = query.order_by(Vehicle.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()
