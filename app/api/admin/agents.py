import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.route import Route
from app.models.schedule import Trip
from app.models.user import User
from app.schemas.user import UserResponse

router = APIRouter(prefix="/agents", tags=["Admin - Agents"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateAgentRequest(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str | None = Field(None, min_length=8)
    assigned_terminal_id: uuid.UUID | None = None


class UpdateAgentRequest(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)
    is_active: bool | None = None
    assigned_terminal_id: uuid.UUID | None = None


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
        assigned_terminal_id=data.assigned_terminal_id,
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
        # Load terminal for each agent
        terminal_name = None
        if user.assigned_terminal_id:
            from app.models.route import Terminal
            term_q = await db.execute(select(Terminal).where(Terminal.id == user.assigned_terminal_id))
            term = term_q.scalar_one_or_none()
            if term:
                terminal_name = f"{term.name} ({term.city})"
        items.append({
            "id": str(user.id), "email": user.email, "phone": user.phone,
            "first_name": user.first_name, "last_name": user.last_name,
            "is_active": user.is_active, "created_at": str(user.created_at),
            "booking_count": booking_count,
            "last_booking": str(last_booking) if last_booking else None,
            "assigned_terminal_id": str(user.assigned_terminal_id) if user.assigned_terminal_id else None,
            "terminal_name": terminal_name,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Agent detail endpoints
# ---------------------------------------------------------------------------

@router.get("/{user_id}", dependencies=[AdminUser])
async def get_agent_detail(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.AGENT)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Agent not found")

    # Stats: total bookings, total revenue, bookings this month
    stats_q = await db.execute(
        select(
            func.count(Booking.id).label("total_bookings"),
            func.coalesce(func.sum(Booking.total_amount), 0).label("total_revenue"),
        ).where(Booking.booked_by_user_id == user_id)
    )
    stats = stats_q.one()

    # Bookings this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_q = await db.execute(
        select(func.count(Booking.id)).where(
            Booking.booked_by_user_id == user_id,
            Booking.created_at >= month_start,
        )
    )
    bookings_this_month = month_q.scalar() or 0

    # Recent bookings (last 10) with route name
    recent_q = await db.execute(
        select(
            Booking.reference,
            Route.name.label("route_name"),
            Trip.departure_date,
            Booking.status,
            Booking.total_amount,
            Booking.created_at,
        )
        .join(Trip, Trip.id == Booking.trip_id)
        .join(Route, Route.id == Trip.route_id)
        .where(Booking.booked_by_user_id == user_id)
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
            "created_at": str(row.created_at),
        }
        for row in recent_q.all()
    ]

    # Get terminal info
    terminal_info = None
    if user.assigned_terminal_id:
        from app.models.route import Terminal
        term_q = await db.execute(select(Terminal).where(Terminal.id == user.assigned_terminal_id))
        term = term_q.scalar_one_or_none()
        if term:
            terminal_info = {"id": str(term.id), "name": term.name, "city": term.city}

    return {
        "user": UserResponse.model_validate(user),
        "stats": {
            "total_bookings": stats.total_bookings,
            "total_revenue": float(stats.total_revenue),
            "bookings_this_month": bookings_this_month,
        },
        "recent_bookings": recent_bookings,
        "assigned_terminal": terminal_info,
    }


@router.put("/{user_id}", dependencies=[AdminUser])
async def update_agent(user_id: uuid.UUID, data: UpdateAgentRequest, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.AGENT)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Agent not found")

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
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", dependencies=[AdminUser])
async def delete_agent(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == UserRole.AGENT)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Agent not found")

    user.is_active = False
    await db.flush()
    return {"message": "Agent deactivated"}
