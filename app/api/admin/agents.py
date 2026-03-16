import secrets
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["Admin - Agents"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateAgentRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str | None = Field(None, min_length=8)


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_agent(data: CreateAgentRequest, db: DBSession):
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
        password_hash=hash_password(password), role=UserRole.AGENT, is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.get("", dependencies=[AdminUser])
async def list_agents(
    db: DBSession, search: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    query = select(
        User,
        func.count(Booking.id).label("booking_count"),
        func.max(Booking.created_at).label("last_booking"),
    ).outerjoin(Booking, Booking.booked_by_user_id == User.id).where(
        User.role == UserRole.AGENT
    ).group_by(User.id)

    if search:
        p = f"%{search}%"
        query = query.where(User.first_name.ilike(p) | User.last_name.ilike(p) | User.email.ilike(p) | User.phone.ilike(p))

    count_q = select(func.count()).select_from(select(User.id).where(User.role == UserRole.AGENT).subquery())
    if search:
        p = f"%{search}%"
        count_q = select(func.count()).select_from(
            select(User.id).where(User.role == UserRole.AGENT,
                User.first_name.ilike(p) | User.last_name.ilike(p) | User.email.ilike(p) | User.phone.ilike(p)
            ).subquery()
        )
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for user, booking_count, last_booking in result.all():
        items.append({
            "id": str(user.id), "email": user.email, "phone": user.phone,
            "first_name": user.first_name, "last_name": user.last_name,
            "is_active": user.is_active, "created_at": str(user.created_at),
            "booking_count": booking_count,
            "last_booking": str(last_booking) if last_booking else None,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}
