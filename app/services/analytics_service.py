from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.payment import Payment
from app.models.user import User


async def get_dashboard_stats(db: AsyncSession) -> dict:
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_bookings = (await db.execute(select(func.count(Booking.id)))).scalar() or 0
    total_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "successful"
            )
        )
    ).scalar()

    return {
        "total_users": total_users,
        "total_bookings": total_bookings,
        "total_revenue": float(total_revenue or 0),
    }


async def get_bookings_by_status(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(
            Booking.status,
            func.count(Booking.id).label("count"),
        ).group_by(Booking.status)
    )
    return [{"status": row.status, "count": row.count} for row in result.all()]
