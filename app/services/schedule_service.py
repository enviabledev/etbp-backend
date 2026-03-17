import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import SeatStatus
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.schedule import Trip, TripSeat

LOCK_DURATION_MINUTES = 5


async def get_trip_seats(db: AsyncSession, trip_id: uuid.UUID) -> dict:
    """Get the full seat map for a trip, releasing any expired locks first."""
    result = await db.execute(
        select(Trip).options(selectinload(Trip.seats)).where(Trip.id == trip_id)
    )
    trip = result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")

    # Release expired locks in-place
    now = datetime.now(timezone.utc)
    released_count = 0
    for seat in trip.seats:
        if (
            seat.status == SeatStatus.LOCKED
            and seat.locked_until
            and seat.locked_until.replace(tzinfo=timezone.utc) < now
        ):
            seat.status = SeatStatus.AVAILABLE
            seat.locked_by_user_id = None
            seat.locked_until = None
            released_count += 1

    if released_count:
        trip.available_seats += released_count
        await db.flush()

    seats = sorted(trip.seats, key=lambda s: (s.seat_row or 0, s.seat_column or 0, s.seat_number))

    # Derive counts from actual seat records for consistency
    actual_total = len(trip.seats)
    actual_available = sum(1 for s in trip.seats if s.status == SeatStatus.AVAILABLE)

    # Sync the stored counts if they drifted
    if trip.total_seats != actual_total or trip.available_seats != actual_available:
        trip.total_seats = actual_total
        trip.available_seats = actual_available
        await db.flush()

    return {
        "trip_id": trip.id,
        "total_seats": actual_total,
        "available_seats": actual_available,
        "seats": [
            {
                "id": s.id,
                "seat_number": s.seat_number,
                "seat_row": s.seat_row,
                "seat_column": s.seat_column,
                "seat_type": s.seat_type,
                "price_modifier": float(s.price_modifier),
                "status": s.status,
            }
            for s in seats
        ],
    }


async def lock_seats(
    db: AsyncSession,
    trip_id: uuid.UUID,
    seat_ids: list[uuid.UUID],
    user_id: uuid.UUID,
) -> dict:
    """Lock multiple seats for a user during checkout. Returns 409 if any are unavailable."""
    # Verify trip exists and is bookable
    trip_result = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = trip_result.scalar_one_or_none()
    if not trip:
        raise NotFoundError("Trip not found")
    if trip.status not in ("scheduled", "boarding"):
        raise BadRequestError("Trip is not available for booking")

    # Fetch requested seats
    seats_result = await db.execute(
        select(TripSeat).where(
            TripSeat.id.in_(seat_ids),
            TripSeat.trip_id == trip_id,
        )
    )
    seats = list(seats_result.scalars().all())

    if len(seats) != len(seat_ids):
        raise NotFoundError("One or more seats not found for this trip")

    now = datetime.now(timezone.utc)
    locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
    unavailable = []

    for seat in seats:
        if seat.status == SeatStatus.BOOKED:
            unavailable.append(seat.seat_number)
        elif seat.status == SeatStatus.LOCKED:
            # Already locked — check if expired or locked by same user
            if seat.locked_until and seat.locked_until.replace(tzinfo=timezone.utc) < now:
                pass  # Expired lock, will re-lock below
            elif seat.locked_by_user_id == user_id:
                pass  # Same user, extend the lock
            else:
                unavailable.append(seat.seat_number)

    if unavailable:
        raise ConflictError(
            f"Seats already taken: {', '.join(unavailable)}"
        )

    # Apply locks
    locked_ids = []
    for seat in seats:
        seat.status = SeatStatus.LOCKED
        seat.locked_by_user_id = user_id
        seat.locked_until = locked_until
        locked_ids.append(seat.id)

    await db.flush()

    return {
        "locked_seats": locked_ids,
        "locked_until": locked_until,
        "message": f"{len(locked_ids)} seat(s) locked for {LOCK_DURATION_MINUTES} minutes",
    }
