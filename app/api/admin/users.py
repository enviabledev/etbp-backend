import secrets
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import selectinload

from app.core.constants import GenderType, UserRole
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.driver import Driver
from app.models.notification import AuditLog
from app.models.payment import Payment, Wallet
from app.models.route import Route
from app.models.schedule import Trip
from app.models.user import User
from app.schemas.user import AdminUserUpdateRequest, UserResponse

router = APIRouter(prefix="/users", tags=["Admin - Users"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class UpdateCustomerRequest(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)
    gender: GenderType | None = None
    date_of_birth: date | None = None
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=20)
    is_active: bool | None = None


class UpdateAdminRequest(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)
    role: UserRole | None = None
    is_active: bool | None = None


class CreateAdminRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str = Field(..., min_length=8)
    role: UserRole


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

@router.get("", dependencies=[AdminUser])
async def list_users(
    db: DBSession,
    role: UserRole | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(User)
    if role:
        query = query.where(User.role == role.value)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            User.email.ilike(pattern)
            | User.first_name.ilike(pattern)
            | User.last_name.ilike(pattern)
            | User.phone.ilike(pattern)
        )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(User.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()
    return {
        "items": [UserResponse.model_validate(u) for u in users],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/customers", dependencies=[AdminUser])
async def list_customers(
    db: DBSession, search: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    query = select(
        User,
        func.count(Booking.id).label("booking_count"),
        func.max(Booking.created_at).label("last_booking"),
    ).outerjoin(Booking, Booking.user_id == User.id).where(
        User.role == UserRole.PASSENGER
    ).group_by(User.id)
    if search:
        p = f"%{search}%"
        query = query.where(User.first_name.ilike(p) | User.last_name.ilike(p) | User.email.ilike(p) | User.phone.ilike(p))

    count_q = select(func.count()).select_from(
        select(User.id).where(User.role == UserRole.PASSENGER).subquery()
    )
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for user, bc, lb in result.all():
        items.append({
            **UserResponse.model_validate(user).model_dump(),
            "booking_count": bc, "last_booking": str(lb) if lb else None,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Customer detail endpoints (before /{user_id})
# ---------------------------------------------------------------------------

@router.get("/customers/{user_id}", dependencies=[AdminUser])
async def get_customer_detail(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.PASSENGER)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Customer not found")

    # Booking stats
    stats_q = await db.execute(
        select(
            func.count(Booking.id).label("total"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("total_spent"),
        ).where(Booking.user_id == user_id)
    )
    stats = stats_q.one()

    # Favourite route: group by route_id, count, pick top
    fav_q = await db.execute(
        select(
            Route.name,
            func.count(Booking.id).label("cnt"),
        )
        .join(Trip, Trip.id == Booking.trip_id)
        .join(Route, Route.id == Trip.route_id)
        .where(Booking.user_id == user_id)
        .group_by(Route.id, Route.name)
        .order_by(func.count(Booking.id).desc())
        .limit(1)
    )
    fav_row = fav_q.first()
    favourite_route = fav_row.name if fav_row else None

    # Recent bookings (last 10) with route name
    recent_q = await db.execute(
        select(
            Booking.reference,
            Route.name.label("route_name"),
            Trip.departure_date,
            Booking.status,
            Booking.total_amount,
        )
        .join(Trip, Trip.id == Booking.trip_id)
        .join(Route, Route.id == Trip.route_id)
        .where(Booking.user_id == user_id)
        .order_by(Booking.created_at.desc())
        .limit(10)
    )
    recent_bookings = [
        {
            "reference": row.reference,
            "route_name": row.route_name,
            "departure_date": str(row.departure_date),
            "status": row.status,
            "amount": float(row.total_amount),
        }
        for row in recent_q.all()
    ]

    # Wallet
    wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = wallet_result.scalar_one_or_none()

    return {
        "user": UserResponse.model_validate(user),
        "stats": {
            "total_bookings": stats.total,
            "total_spent": float(stats.total_spent),
            "favourite_route": favourite_route,
        },
        "recent_bookings": recent_bookings,
        "wallet_balance": float(wallet.balance) if wallet else 0.0,
    }


@router.put("/customers/{user_id}", dependencies=[AdminUser])
async def update_customer(user_id: uuid.UUID, data: UpdateCustomerRequest, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.PASSENGER)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Customer not found")

    update_data = data.model_dump(exclude_unset=True)

    # Check email uniqueness if changing
    if "email" in update_data and update_data["email"] is not None:
        email_lower = update_data["email"].lower()
        existing = await db.execute(
            select(User).where(User.email == email_lower, User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("A user with this email already exists")
        update_data["email"] = email_lower

    # Check phone uniqueness if changing
    if "phone" in update_data and update_data["phone"] is not None:
        existing = await db.execute(
            select(User).where(User.phone == update_data["phone"], User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("A user with this phone number already exists")

    for field, value in update_data.items():
        if isinstance(value, GenderType):
            value = value.value
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/customers/{user_id}", dependencies=[AdminUser])
async def delete_customer(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.PASSENGER)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Customer not found")

    user.is_active = False
    await db.flush()
    return {"message": "Customer deactivated"}


# ---------------------------------------------------------------------------
# Admin detail endpoints (before /{user_id})
# ---------------------------------------------------------------------------

@router.get("/admins", dependencies=[AdminUser])
async def list_admin_users(
    db: DBSession, search: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    admin_roles = [UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER]
    query = select(User).where(User.role.in_([r.value for r in admin_roles]))
    if search:
        p = f"%{search}%"
        query = query.where(User.first_name.ilike(p) | User.last_name.ilike(p) | User.email.ilike(p))
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return {
        "items": [UserResponse.model_validate(u) for u in result.scalars().all()],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/admins/{user_id}", dependencies=[AdminUser])
async def get_admin_detail(user_id: uuid.UUID, db: DBSession):
    admin_roles = [UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value, UserRole.FLEET_MANAGER.value]
    result = await db.execute(
        select(User).where(User.id == user_id, User.role.in_(admin_roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Admin user not found")

    # Recent audit logs (last 20)
    audit_q = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )
    audit_logs = [
        {
            "id": str(log.id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "ip_address": log.ip_address,
            "created_at": str(log.created_at),
        }
        for log in audit_q.scalars().all()
    ]

    return {
        "user": UserResponse.model_validate(user),
        "recent_audit_logs": audit_logs,
    }


@router.put("/admins/{user_id}", dependencies=[AdminUser])
async def update_admin(user_id: uuid.UUID, data: UpdateAdminRequest, db: DBSession):
    admin_roles = [UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value, UserRole.FLEET_MANAGER.value]
    result = await db.execute(
        select(User).where(User.id == user_id, User.role.in_(admin_roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Admin user not found")

    update_data = data.model_dump(exclude_unset=True)

    # Validate role if provided
    if "role" in update_data and update_data["role"] is not None:
        allowed = {UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER}
        if update_data["role"] not in allowed:
            raise BadRequestError("Role must be one of: admin, super_admin, fleet_manager")

    # Check email uniqueness if changing
    if "email" in update_data and update_data["email"] is not None:
        email_lower = update_data["email"].lower()
        existing = await db.execute(
            select(User).where(User.email == email_lower, User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("A user with this email already exists")
        update_data["email"] = email_lower

    # Check phone uniqueness if changing
    if "phone" in update_data and update_data["phone"] is not None:
        existing = await db.execute(
            select(User).where(User.phone == update_data["phone"], User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("A user with this phone number already exists")

    for field, value in update_data.items():
        if isinstance(value, UserRole):
            value = value.value
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/admins", status_code=201, dependencies=[AdminUser])
async def create_admin(data: CreateAdminRequest, db: DBSession):
    allowed_roles = {UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.FLEET_MANAGER}
    if data.role not in allowed_roles:
        raise BadRequestError("Role must be one of: admin, super_admin, fleet_manager")

    email_lower = data.email.lower()
    existing = await db.execute(select(User).where(User.email == email_lower))
    if existing.scalar_one_or_none():
        raise ConflictError("A user with this email already exists")

    phone_check = await db.execute(select(User).where(User.phone == data.phone))
    if phone_check.scalar_one_or_none():
        raise ConflictError("A user with this phone number already exists")

    user = User(
        email=email_lower,
        phone=data.phone,
        first_name=data.first_name,
        last_name=data.last_name,
        password_hash=hash_password(data.password),
        role=data.role.value,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# Generic user endpoints (/{user_id} must come AFTER specific paths)
# ---------------------------------------------------------------------------

@router.get("/{user_id}", dependencies=[AdminUser])
async def get_user_detail(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    # Booking stats
    booking_stats = await db.execute(
        select(
            func.count(Booking.id).label("total"),
            func.sum(
                func.cast(Booking.status == "confirmed", Integer)
            ).label("confirmed"),
            func.sum(
                func.cast(Booking.status == "cancelled", Integer)
            ).label("cancelled"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("total_spent"),
        ).where(Booking.user_id == user_id)
    )
    stats = booking_stats.one()

    # Wallet
    wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = wallet_result.scalar_one_or_none()

    return {
        "user": UserResponse.model_validate(user),
        "stats": {
            "total_bookings": stats.total,
            "confirmed_bookings": int(stats.confirmed or 0),
            "cancelled_bookings": int(stats.cancelled or 0),
            "total_spent": float(stats.total_spent),
        },
        "wallet_balance": float(wallet.balance) if wallet else 0.0,
    }


@router.put("/{user_id}", response_model=UserResponse, dependencies=[AdminUser])
async def update_user(user_id: uuid.UUID, data: AdminUserUpdateRequest, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        if isinstance(value, UserRole):
            value = value.value
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return user


@router.put("/{user_id}/deactivate", dependencies=[AdminUser])
async def deactivate_user(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    if user.role == UserRole.SUPER_ADMIN:
        raise BadRequestError("Cannot deactivate a super admin")
    user.is_active = False
    await db.flush()
    return {"id": str(user.id), "is_active": False}


@router.put("/{user_id}/activate", dependencies=[AdminUser])
async def activate_user(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    user.is_active = True
    await db.flush()
    return {"id": str(user.id), "is_active": True}


@router.put("/{user_id}/role", dependencies=[AdminUser])
async def change_user_role(
    user_id: uuid.UUID,
    role: UserRole,
    db: DBSession,
):
    """Change a user's role. Only super_admin can promote to admin/super_admin."""
    from app.core.permissions import ADMIN_ROLES

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    old_role = user.role
    user.role = role.value
    await db.flush()
    return {
        "id": str(user.id),
        "email": user.email,
        "old_role": old_role,
        "new_role": user.role,
    }
