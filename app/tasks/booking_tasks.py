import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.constants import BookingStatus, SeatStatus
from app.database import async_session_factory
from app.models.booking import Booking
from app.models.schedule import Trip, TripSeat
from app.tasks.celery_app import celery_app


async def _expire_pending_bookings():
    """Expire bookings that have been pending for too long (30 minutes)."""
    async with async_session_factory() as db:
        cutoff = datetime.now(timezone.utc)
        result = await db.execute(
            select(Booking).where(
                Booking.status == BookingStatus.PENDING,
                Booking.created_at < cutoff,
            )
        )
        bookings = result.scalars().all()

        for booking in bookings:
            elapsed = (cutoff - booking.created_at.replace(tzinfo=timezone.utc)).total_seconds()
            if elapsed > 1800:  # 30 minutes
                booking.status = BookingStatus.EXPIRED

                # Release seats
                from sqlalchemy.orm import selectinload
                booking_with_passengers = await db.execute(
                    select(Booking)
                    .options(selectinload(Booking.passengers))
                    .where(Booking.id == booking.id)
                )
                full_booking = booking_with_passengers.scalar_one()
                seat_ids = [p.seat_id for p in full_booking.passengers]

                if seat_ids:
                    await db.execute(
                        update(TripSeat)
                        .where(TripSeat.id.in_(seat_ids))
                        .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
                    )
                    trip_result = await db.execute(
                        select(Trip).where(Trip.id == booking.trip_id)
                    )
                    trip = trip_result.scalar_one()
                    trip.available_seats += len(seat_ids)

        await db.commit()


async def _release_expired_locks():
    """Release seat locks that have expired."""
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        await db.execute(
            update(TripSeat)
            .where(
                TripSeat.status == SeatStatus.LOCKED,
                TripSeat.locked_until < now,
            )
            .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
        )
        await db.commit()


@celery_app.task(name="expire_pending_bookings")
def expire_pending_bookings():
    asyncio.run(_expire_pending_bookings())


@celery_app.task(name="release_expired_seat_locks")
def release_expired_seat_locks():
    asyncio.run(_release_expired_locks())
