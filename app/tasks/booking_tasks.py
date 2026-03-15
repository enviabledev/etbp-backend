import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.constants import BookingStatus, SeatStatus
from app.database import async_session_factory
from app.models.booking import Booking
from app.models.route import Route
from app.models.schedule import Trip, TripSeat
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _expire_pending_bookings():
    """Expire bookings pending for more than 30 minutes. Release their seats."""
    async with async_session_factory() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = await db.execute(
            select(Booking)
            .options(selectinload(Booking.passengers))
            .where(
                Booking.status == BookingStatus.PENDING,
                Booking.created_at < cutoff,
            )
        )
        bookings = result.scalars().all()

        expired_count = 0
        for booking in bookings:
            booking.status = BookingStatus.EXPIRED
            seat_ids = [p.seat_id for p in booking.passengers]

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

            expired_count += 1

        await db.commit()
        if expired_count:
            logger.info("Expired %d pending bookings", expired_count)


async def _release_expired_locks():
    """Release seat locks that have passed their locked_until time."""
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            update(TripSeat)
            .where(
                TripSeat.status == SeatStatus.LOCKED,
                TripSeat.locked_until < now,
            )
            .values(status=SeatStatus.AVAILABLE, locked_by_user_id=None, locked_until=None)
        )
        await db.commit()
        if result.rowcount:  # type: ignore[union-attr]
            logger.info("Released %d expired seat locks", result.rowcount)


async def _send_trip_reminders():
    """Send reminders for trips departing tomorrow."""
    from app.services.notification_service import notify_trip_reminder

    tomorrow = date.today() + timedelta(days=1)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Booking)
            .options(
                selectinload(Booking.passengers),
                selectinload(Booking.user),
                selectinload(Booking.trip)
                .selectinload(Trip.route)
                .selectinload(Route.origin_terminal),
            )
            .where(
                Booking.status == BookingStatus.CONFIRMED,
            )
            .join(Trip, Booking.trip_id == Trip.id)
            .where(Trip.departure_date == tomorrow)
        )
        bookings = result.scalars().all()

        sent = 0
        for booking in bookings:
            trip = booking.trip
            route = trip.route
            user = booking.user

            seat_numbers = ", ".join(
                p.qr_code_data.split("-")[1] if p.qr_code_data and "-" in p.qr_code_data else "N/A"
                for p in booking.passengers
            )

            primary = next((p for p in booking.passengers if p.is_primary), booking.passengers[0] if booking.passengers else None)
            name = f"{primary.first_name} {primary.last_name}" if primary else user.full_name

            await notify_trip_reminder(
                db,
                user_id=booking.user_id,
                booking_reference=booking.reference,
                passenger_name=name,
                email=booking.contact_email or user.email,
                phone=booking.contact_phone or user.phone,
                route_name=route.name,
                departure_date=trip.departure_date.strftime("%d %b %Y"),
                departure_time=trip.departure_time.strftime("%H:%M"),
                terminal_name=route.origin_terminal.name if route.origin_terminal else "N/A",
                seat_numbers=seat_numbers,
            )
            sent += 1

        await db.commit()
        if sent:
            logger.info("Sent %d trip reminders for %s", sent, tomorrow)


@celery_app.task(name="expire_pending_bookings")
def expire_pending_bookings():
    asyncio.run(_expire_pending_bookings())


@celery_app.task(name="release_expired_seat_locks")
def release_expired_seat_locks():
    asyncio.run(_release_expired_locks())


@celery_app.task(name="send_trip_reminders")
def send_trip_reminders():
    asyncio.run(_send_trip_reminders())
