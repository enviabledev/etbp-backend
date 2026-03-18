import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, and_
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models.booking import Booking
from app.models.schedule import Trip
from app.models.route import Route
from app.services.push_notification_service import send_push_to_user

logger = logging.getLogger(__name__)


async def send_trip_reminders() -> int:
    """Send 24h and 1h trip reminders. Run every 15 minutes."""
    now = datetime.now(timezone.utc)
    sent = 0

    async with async_session_factory() as db:
        try:
            # ── 24-hour reminders ──
            window_start = now + timedelta(hours=23)
            window_end = now + timedelta(hours=25)

            result = await db.execute(
                select(Booking)
                .options(
                    selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.origin_terminal),
                    selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.destination_terminal),
                )
                .join(Trip, Booking.trip_id == Trip.id)
                .where(
                    Booking.status.in_(["confirmed", "checked_in"]),
                    Booking.reminder_24h_sent == False,  # noqa: E712
                    Trip.departure_date >= window_start.date(),
                )
            )
            bookings = result.scalars().all()

            for booking in bookings:
                trip = booking.trip
                if not trip:
                    continue
                dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
                if not (window_start <= dep_dt <= window_end):
                    continue

                route_name = trip.route.name if trip.route else "your trip"
                terminal = trip.route.origin_terminal.name if trip.route and trip.route.origin_terminal else ""
                dep_time = trip.departure_time.strftime("%I:%M %p") if trip.departure_time else ""

                await send_push_to_user(
                    db, booking.user_id,
                    title="Trip Tomorrow",
                    body=f"Your trip {route_name} departs tomorrow at {dep_time} from {terminal}.",
                    data={"type": "trip_reminder", "booking_ref": booking.reference},
                    app_type="customer",
                )
                booking.reminder_24h_sent = True
                sent += 1

            # ── 1-hour reminders ──
            window_start_1h = now + timedelta(minutes=50)
            window_end_1h = now + timedelta(minutes=70)

            result2 = await db.execute(
                select(Booking)
                .options(
                    selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.origin_terminal),
                    selectinload(Booking.trip).selectinload(Trip.route).selectinload(Route.destination_terminal),
                )
                .join(Trip, Booking.trip_id == Trip.id)
                .where(
                    Booking.status.in_(["confirmed", "checked_in"]),
                    Booking.reminder_1h_sent == False,  # noqa: E712
                    Trip.departure_date == now.date(),
                )
            )
            bookings_1h = result2.scalars().all()

            for booking in bookings_1h:
                trip = booking.trip
                if not trip:
                    continue
                dep_dt = datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
                if not (window_start_1h <= dep_dt <= window_end_1h):
                    continue

                route_name = trip.route.name if trip.route else "your trip"
                terminal = trip.route.origin_terminal.name if trip.route and trip.route.origin_terminal else ""

                await send_push_to_user(
                    db, booking.user_id,
                    title="Trip in 1 Hour",
                    body=f"Your trip {route_name} departs in 1 hour from {terminal}. Time to head out!",
                    data={"type": "trip_reminder_1h", "booking_ref": booking.reference},
                    app_type="customer",
                )
                booking.reminder_1h_sent = True
                sent += 1

            await db.commit()
            if sent:
                logger.info("Sent %d trip reminders", sent)
        except Exception:
            await db.rollback()
            logger.exception("Error sending trip reminders")

    return sent
