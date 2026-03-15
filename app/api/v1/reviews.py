import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking
from app.models.review import TripReview

router = APIRouter(prefix="/reviews", tags=["Reviews"])


class CreateReviewRequest(BaseModel):
    booking_id: uuid.UUID
    overall_rating: int = Field(..., ge=1, le=5)
    driver_rating: int | None = Field(None, ge=1, le=5)
    bus_condition_rating: int | None = Field(None, ge=1, le=5)
    punctuality_rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None


@router.post("", status_code=201)
async def create_review(
    data: CreateReviewRequest, db: DBSession, current_user: CurrentUser
):
    booking_result = await db.execute(
        select(Booking).where(
            Booking.id == data.booking_id, Booking.user_id == current_user.id
        )
    )
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status != "completed":
        raise BadRequestError("Can only review completed trips")

    existing = await db.execute(
        select(TripReview).where(TripReview.booking_id == data.booking_id)
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Review already submitted for this booking")

    review = TripReview(
        booking_id=booking.id,
        user_id=current_user.id,
        trip_id=booking.trip_id,
        overall_rating=data.overall_rating,
        driver_rating=data.driver_rating,
        bus_condition_rating=data.bus_condition_rating,
        punctuality_rating=data.punctuality_rating,
        comment=data.comment,
    )
    db.add(review)
    await db.flush()
    await db.refresh(review)
    return {"id": str(review.id), "message": "Review submitted"}


@router.get("/trip/{trip_id}")
async def get_trip_reviews(
    trip_id: uuid.UUID,
    db: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = (
        select(TripReview)
        .where(TripReview.trip_id == trip_id, TripReview.is_visible == True)  # noqa: E712
        .order_by(TripReview.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return result.scalars().all()
