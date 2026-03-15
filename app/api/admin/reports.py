from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.dependencies import DBSession, require_role
from app.models.booking import Booking
from app.models.payment import Payment
from app.models.user import User

router = APIRouter(prefix="/reports", tags=["Admin - Reports"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("/dashboard", dependencies=[AdminUser])
async def dashboard_summary(db: DBSession):
    total_users = await db.execute(select(func.count(User.id)))
    total_bookings = await db.execute(select(func.count(Booking.id)))
    total_revenue = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "successful"
        )
    )
    return {
        "total_users": total_users.scalar(),
        "total_bookings": total_bookings.scalar(),
        "total_revenue": float(total_revenue.scalar()),
    }


@router.get("/revenue", dependencies=[AdminUser])
async def revenue_report(
    db: DBSession,
    from_date: date | None = None,
    to_date: date | None = None,
):
    query = select(
        func.date(Payment.paid_at).label("date"),
        func.sum(Payment.amount).label("amount"),
        func.count(Payment.id).label("count"),
    ).where(Payment.status == "successful")

    if from_date:
        query = query.where(func.date(Payment.paid_at) >= from_date)
    if to_date:
        query = query.where(func.date(Payment.paid_at) <= to_date)

    query = query.group_by(func.date(Payment.paid_at)).order_by(
        func.date(Payment.paid_at).desc()
    )
    result = await db.execute(query)
    return [
        {"date": str(row.date), "amount": float(row.amount), "count": row.count}
        for row in result.all()
    ]
