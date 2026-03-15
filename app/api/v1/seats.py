import uuid

from fastapi import APIRouter

from app.dependencies import CurrentUser, DBSession
from app.schemas.schedule import LockSeatsRequest, LockSeatsResponse, SeatMapResponse
from app.services import schedule_service

router = APIRouter(prefix="/trips", tags=["Trips - Seats"])


@router.get("/{trip_id}/seats", response_model=SeatMapResponse)
async def get_seat_map(trip_id: uuid.UUID, db: DBSession):
    """Get the seat map for a trip with real-time availability.
    Automatically releases any expired seat locks."""
    return await schedule_service.get_trip_seats(db, trip_id)


@router.post("/{trip_id}/seats/lock", response_model=LockSeatsResponse)
async def lock_seats(
    trip_id: uuid.UUID,
    data: LockSeatsRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    """Lock selected seats for 5 minutes during checkout.
    Returns 409 if any seats are already locked by another user or booked."""
    return await schedule_service.lock_seats(
        db, trip_id, data.seat_ids, current_user.id
    )
