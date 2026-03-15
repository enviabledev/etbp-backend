import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.payment import Payment, Wallet
from app.models.user import User
from app.schemas.user import AdminUserUpdateRequest, UserResponse

router = APIRouter(prefix="/users", tags=["Admin - Users"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


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
                func.cast(Booking.status == "confirmed", __import__("sqlalchemy").Integer)
            ).label("confirmed"),
            func.sum(
                func.cast(Booking.status == "cancelled", __import__("sqlalchemy").Integer)
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
