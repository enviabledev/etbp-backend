import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.driver import Driver
from app.models.user import User

router = APIRouter(prefix="/drivers", tags=["Admin - Drivers"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER))


class CreateDriverRequest(BaseModel):
    user_id: uuid.UUID
    license_number: str = Field(..., max_length=50)
    license_expiry: date
    license_class: str | None = None
    years_experience: int | None = None
    assigned_terminal_id: uuid.UUID | None = None


class UpdateDriverRequest(BaseModel):
    license_number: str | None = None
    license_expiry: date | None = None
    license_class: str | None = None
    years_experience: int | None = None
    medical_check_expiry: date | None = None
    is_available: bool | None = None
    assigned_terminal_id: uuid.UUID | None = None


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_driver(data: CreateDriverRequest, db: DBSession):
    existing = await db.execute(
        select(Driver).where(Driver.user_id == data.user_id)
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Driver profile already exists for this user")

    user_result = await db.execute(select(User).where(User.id == data.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    user.role = UserRole.DRIVER
    driver = Driver(**data.model_dump())
    db.add(driver)
    await db.flush()
    await db.refresh(driver)
    return driver


@router.get("", dependencies=[AdminUser])
async def list_drivers(
    db: DBSession,
    is_available: bool | None = None,
    search: str | None = None,
    terminal_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Driver).options(
        selectinload(Driver.user),
        selectinload(Driver.assigned_terminal),
    )
    if is_available is not None:
        query = query.where(Driver.is_available == is_available)
    if terminal_id:
        query = query.where(Driver.assigned_terminal_id == terminal_id)
    if search:
        query = query.join(User, Driver.user_id == User.id).where(
            User.first_name.ilike(f"%{search}%")
            | User.last_name.ilike(f"%{search}%")
            | User.phone.ilike(f"%{search}%")
            | Driver.license_number.ilike(f"%{search}%")
        )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(Driver.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/{driver_id}", dependencies=[AdminUser])
async def get_driver(driver_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Driver)
        .options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
        .where(Driver.id == driver_id)
    )
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundError("Driver not found")
    return driver


@router.put("/{driver_id}", dependencies=[AdminUser])
async def update_driver(driver_id: uuid.UUID, data: UpdateDriverRequest, db: DBSession):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundError("Driver not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(driver, field, value)
    await db.flush()
    await db.refresh(driver)
    return driver
