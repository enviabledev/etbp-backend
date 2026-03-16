import secrets
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.dependencies import DBSession, require_role
from app.models.driver import Driver
from app.models.user import User

router = APIRouter(prefix="/drivers", tags=["Admin - Drivers"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER))


class CreateDriverRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str | None = Field(None, min_length=8)
    license_number: str = Field(..., max_length=50)
    license_expiry: date
    license_class: str | None = None
    years_experience: int | None = None
    medical_check_expiry: date | None = None
    assigned_terminal_id: uuid.UUID | None = None


class UpdateDriverRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    license_number: str | None = None
    license_expiry: date | None = None
    license_class: str | None = None
    years_experience: int | None = None
    medical_check_expiry: date | None = None
    is_available: bool | None = None
    assigned_terminal_id: uuid.UUID | None = None


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_driver(data: CreateDriverRequest, db: DBSession):
    email_check = await db.execute(select(User).where(User.email == data.email.lower()))
    if email_check.scalar_one_or_none():
        raise ConflictError("A user with this email already exists")
    phone_check = await db.execute(select(User).where(User.phone == data.phone))
    if phone_check.scalar_one_or_none():
        raise ConflictError("A user with this phone number already exists")

    password = data.password or secrets.token_urlsafe(12)
    user = User(
        email=data.email.lower(), phone=data.phone,
        first_name=data.first_name, last_name=data.last_name,
        password_hash=hash_password(password), role=UserRole.DRIVER, is_active=True,
    )
    db.add(user)
    await db.flush()

    driver = Driver(
        user_id=user.id, license_number=data.license_number,
        license_expiry=data.license_expiry, license_class=data.license_class,
        years_experience=data.years_experience,
        medical_check_expiry=data.medical_check_expiry,
        assigned_terminal_id=data.assigned_terminal_id,
    )
    db.add(driver)
    await db.flush()

    result = await db.execute(
        select(Driver).options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
        .where(Driver.id == driver.id)
    )
    return result.scalar_one()


@router.get("", dependencies=[AdminUser])
async def list_drivers(
    db: DBSession, is_available: bool | None = None, search: str | None = None,
    terminal_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    query = select(Driver).options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
    if is_available is not None:
        query = query.where(Driver.is_available == is_available)
    if terminal_id:
        query = query.where(Driver.assigned_terminal_id == terminal_id)
    if search:
        query = query.join(User, Driver.user_id == User.id).where(
            User.first_name.ilike(f"%{search}%") | User.last_name.ilike(f"%{search}%")
            | User.phone.ilike(f"%{search}%") | Driver.license_number.ilike(f"%{search}%")
        )
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()
    query = query.order_by(Driver.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/{driver_id}", dependencies=[AdminUser])
async def get_driver(driver_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Driver).options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
        .where(Driver.id == driver_id)
    )
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundError("Driver not found")
    return driver


@router.put("/{driver_id}", dependencies=[AdminUser])
async def update_driver(driver_id: uuid.UUID, data: UpdateDriverRequest, db: DBSession):
    result = await db.execute(select(Driver).options(selectinload(Driver.user)).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundError("Driver not found")
    update_data = data.model_dump(exclude_unset=True)
    for f in {"first_name", "last_name", "phone"} & update_data.keys():
        setattr(driver.user, f, update_data.pop(f))
    for field, value in update_data.items():
        setattr(driver, field, value)
    await db.flush()
    result = await db.execute(
        select(Driver).options(selectinload(Driver.user), selectinload(Driver.assigned_terminal))
        .where(Driver.id == driver_id)
    )
    return result.scalar_one()
