import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.core.constants import SeatStatus
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.schedule import TripSeat
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/seats", tags=["Seats"])

LOCK_DURATION_MINUTES = 10


@router.post("/{seat_id}/lock", response_model=MessageResponse)
async def lock_seat(seat_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(TripSeat).where(TripSeat.id == seat_id))
    seat = result.scalar_one_or_none()
    if not seat:
        raise NotFoundError("Seat not found")
    if seat.status != SeatStatus.AVAILABLE:
        raise BadRequestError("Seat is not available")

    seat.status = SeatStatus.LOCKED
    seat.locked_by_user_id = current_user.id
    seat.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCK_DURATION_MINUTES)
    await db.flush()
    return MessageResponse(message="Seat locked successfully")


@router.post("/{seat_id}/unlock", response_model=MessageResponse)
async def unlock_seat(seat_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(TripSeat).where(TripSeat.id == seat_id))
    seat = result.scalar_one_or_none()
    if not seat:
        raise NotFoundError("Seat not found")
    if seat.status != SeatStatus.LOCKED or seat.locked_by_user_id != current_user.id:
        raise BadRequestError("You cannot unlock this seat")

    seat.status = SeatStatus.AVAILABLE
    seat.locked_by_user_id = None
    seat.locked_until = None
    await db.flush()
    return MessageResponse(message="Seat unlocked successfully")
