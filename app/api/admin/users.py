import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
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
        query = query.where(
            (User.email.ilike(f"%{search}%"))
            | (User.first_name.ilike(f"%{search}%"))
            | (User.last_name.ilike(f"%{search}%"))
            | (User.phone.ilike(f"%{search}%"))
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


@router.get("/{user_id}", response_model=UserResponse, dependencies=[AdminUser])
async def get_user(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    return user


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
