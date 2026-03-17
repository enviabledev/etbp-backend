"""Background tasks for expiring stale pending bookings and releasing expired seat locks."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.constants import BookingStatus, SeatStatus
from app.database import async_session_factory
from app.models.booking import Booking, BookingPassenger
from app.models.schedule import Trip, TripSeat

logger = logging.getLogger(__name__)

PENDING_EXPIRY_MINUTES = 15


async def expire_pending_bookings() -> int:
    """Expire bookings that have been pending for more than 15 minutes.
    Releases their seats back to available and restores available_seats count."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_EXPIRY_MINUTES)
    expired_count = 0

    async with async_session_factory() as db:
        try:
            # Find expired pending bookings
            result = await db.execute(
                select(Booking).where(
                    Booking.status == BookingStatus.PENDING.value,
                    Booking.created_at < cutoff,
                )
            )
            expired_bookings = result.scalars().all()

            for booking in expired_bookings:
                booking.status = BookingStatus.EXPIRED.value

                # Find seat IDs for this booking's passengers
                passenger_result = await db.execute(
                    select(BookingPassenger.seat_id).where(
                        BookingPassenger.booking_id == booking.id
                    )
                )
                seat_ids = [row for row in passenger_result.scalars().all()]

                if seat_ids:
                    # Release seats back to available
                    await db.execute(
                        update(TripSeat)
                        .where(TripSeat.id.in_(seat_ids))
                        .values(
                            status=SeatStatus.AVAILABLE.value,
                            locked_by_user_id=None,
                            locked_until=None,
                        )
                    )

                    # Restore available_seats count on the trip
                    await db.execute(
                        update(Trip)
                        .where(Trip.id == booking.trip_id)
                        .values(available_seats=Trip.available_seats + len(seat_ids))
                    )

                expired_count += 1

            await db.commit()

            if expired_count:
                logger.info("Expired %d pending bookings", expired_count)

        except Exception:
            await db.rollback()
            logger.exception("Error expiring pending bookings")

    return expired_count


async def release_expired_seat_locks() -> int:
    """Release seat locks that have passed their locked_until time."""
    now = datetime.now(timezone.utc)
    released_count = 0

    async with async_session_factory() as db:
        try:
            # Find expired locked seats
            result = await db.execute(
                select(TripSeat).where(
                    TripSeat.status == SeatStatus.LOCKED.value,
                    TripSeat.locked_until < now,
                )
            )
            expired_seats = result.scalars().all()

            if expired_seats:
                # Group by trip to update available_seats
                trip_counts: dict[str, int] = {}
                for seat in expired_seats:
                    seat.status = SeatStatus.AVAILABLE.value
                    seat.locked_by_user_id = None
                    seat.locked_until = None
                    trip_id_str = str(seat.trip_id)
                    trip_counts[trip_id_str] = trip_counts.get(trip_id_str, 0) + 1
                    released_count += 1

                # Update available_seats on each affected trip
                for trip_id_str, count in trip_counts.items():
                    import uuid as _uuid
                    await db.execute(
                        update(Trip)
                        .where(Trip.id == _uuid.UUID(trip_id_str))
                        .values(available_seats=Trip.available_seats + count)
                    )

                await db.commit()
                logger.info("Released %d expired seat locks", released_count)

        except Exception:
            await db.rollback()
            logger.exception("Error releasing expired seat locks")

    return released_count
