from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.user import User

router = APIRouter(prefix="/reports", tags=["Agent - Reports"])

AgentUser = Annotated[User, Depends(require_role(UserRole.AGENT, UserRole.ADMIN, UserRole.SUPER_ADMIN))]


@router.get("/my-bookings-summary")
async def my_booking_summary(
    db: DBSession,
    current_user: AgentUser,
    from_date: date | None = None,
    to_date: date | None = None,
):
    query = select(
        func.count(Booking.id).label("total"),
        func.coalesce(func.sum(Booking.total_amount), 0).label("total_amount"),
    ).where(Booking.booked_by_user_id == current_user.id)

    if from_date:
        query = query.where(func.date(Booking.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(Booking.created_at) <= to_date)

    result = await db.execute(query)
    row = result.one()
    return {"total_bookings": row.total, "total_amount": float(row.total_amount)}
