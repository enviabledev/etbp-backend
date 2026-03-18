import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.dependencies import CurrentUser, DBSession
from app.models.booking import Booking
from app.models.review import TripReview
from app.models.schedule import Trip
from app.models.user import User

router = APIRouter(prefix="/reviews", tags=["Reviews"])


class CreateReviewRequest(BaseModel):
    overall_rating: int = Field(..., ge=1, le=5)
    driver_rating: int | None = Field(None, ge=1, le=5)
    bus_condition_rating: int | None = Field(None, ge=1, le=5)
    punctuality_rating: int | None = Field(None, ge=1, le=5)
    comfort_rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=500)
    is_anonymous: bool = False


# ── By booking reference ──

@router.post("/booking/{ref}", status_code=201)
async def create_review(ref: str, data: CreateReviewRequest, db: DBSession, current_user: CurrentUser):
    booking_q = await db.execute(
        select(Booking).where(Booking.reference == ref.upper(), Booking.user_id == current_user.id)
    )
    booking = booking_q.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    if booking.status not in ("completed", "checked_in"):
        raise BadRequestError("Can only review completed trips")

    # Check trip actually departed
    trip_q = await db.execute(select(Trip).where(Trip.id == booking.trip_id))
    trip = trip_q.scalar_one()
    if trip.status not in ("completed", "arrived"):
        raise BadRequestError("Trip has not been completed yet")

    # 30-day window
    completed_at = trip.actual_arrival_at or datetime.combine(trip.departure_date, trip.departure_time, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - completed_at > timedelta(days=30):
        raise BadRequestError("Review window has closed (30 days)")

    # One review per booking
    existing = await db.execute(select(TripReview).where(TripReview.booking_id == booking.id))
    if existing.scalar_one_or_none():
        raise ConflictError("Review already submitted for this booking")

    review = TripReview(
        booking_id=booking.id, user_id=current_user.id, trip_id=booking.trip_id,
        driver_id=trip.driver_id,
        overall_rating=data.overall_rating, driver_rating=data.driver_rating,
        bus_condition_rating=data.bus_condition_rating, punctuality_rating=data.punctuality_rating,
        comfort_rating=data.comfort_rating, comment=data.comment, is_anonymous=data.is_anonymous,
    )
    db.add(review)

    # Update driver rating
    if trip.driver_id and data.driver_rating:
        from app.models.driver import Driver
        avg_q = await db.execute(
            select(func.avg(TripReview.driver_rating)).where(
                TripReview.driver_id == trip.driver_id, TripReview.driver_rating.isnot(None)
            )
        )
        # Include this new rating in the average
        current_avg = avg_q.scalar()
        count_q = await db.execute(
            select(func.count(TripReview.id)).where(
                TripReview.driver_id == trip.driver_id, TripReview.driver_rating.isnot(None)
            )
        )
        count = count_q.scalar() or 0
        new_avg = ((float(current_avg or 0) * count) + data.driver_rating) / (count + 1)
        driver_q = await db.execute(select(Driver).where(Driver.id == trip.driver_id))
        driver = driver_q.scalar_one_or_none()
        if driver:
            driver.rating_avg = round(new_avg, 2)

    await db.flush()
    await db.refresh(review)

    # Push notification prompt
    try:
        from app.services.push_notification_service import send_push_to_user
        await send_push_to_user(db, current_user.id, "Thank you!", "Your review has been submitted.", {"type": "review_submitted"}, "customer")
    except Exception:
        pass

    return {"id": str(review.id), "message": "Review submitted. Thank you!"}


@router.get("/booking/{ref}")
async def get_booking_review(ref: str, db: DBSession, current_user: CurrentUser):
    booking_q = await db.execute(select(Booking).where(Booking.reference == ref.upper(), Booking.user_id == current_user.id))
    booking = booking_q.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    review_q = await db.execute(select(TripReview).where(TripReview.booking_id == booking.id))
    review = review_q.scalar_one_or_none()
    if not review:
        raise NotFoundError("No review for this booking")
    return _serialize_review(review)


@router.put("/booking/{ref}")
async def update_review(ref: str, data: CreateReviewRequest, db: DBSession, current_user: CurrentUser):
    booking_q = await db.execute(select(Booking).where(Booking.reference == ref.upper(), Booking.user_id == current_user.id))
    booking = booking_q.scalar_one_or_none()
    if not booking:
        raise NotFoundError("Booking not found")
    review_q = await db.execute(select(TripReview).where(TripReview.booking_id == booking.id))
    review = review_q.scalar_one_or_none()
    if not review:
        raise NotFoundError("No review to update")
    if datetime.now(timezone.utc) - review.created_at > timedelta(days=7):
        raise BadRequestError("Review can only be edited within 7 days of submission")
    review.overall_rating = data.overall_rating
    review.driver_rating = data.driver_rating
    review.bus_condition_rating = data.bus_condition_rating
    review.punctuality_rating = data.punctuality_rating
    review.comfort_rating = data.comfort_rating
    review.comment = data.comment
    review.is_anonymous = data.is_anonymous
    await db.flush()
    return _serialize_review(review)


# ── Public route reviews ──

@router.get("/route/{route_id}")
async def get_route_reviews(
    route_id: uuid.UUID, db: DBSession,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
    sort_by: str = Query("newest"),
):
    base = (
        select(TripReview)
        .join(Trip, TripReview.trip_id == Trip.id)
        .where(Trip.route_id == route_id, TripReview.is_visible == True)  # noqa: E712
    )
    order = TripReview.created_at.desc()
    if sort_by == "highest":
        order = TripReview.overall_rating.desc()
    elif sort_by == "lowest":
        order = TripReview.overall_rating.asc()

    count_q = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_q.scalar() or 0

    result = await db.execute(
        base.options(selectinload(TripReview.user))
        .order_by(order).offset((page - 1) * page_size).limit(page_size)
    )
    reviews = [_serialize_review(r, include_user=True) for r in result.scalars().all()]

    # Aggregates
    agg_q = await db.execute(
        select(
            func.avg(TripReview.overall_rating),
            func.count(TripReview.id),
        ).join(Trip, TripReview.trip_id == Trip.id)
        .where(Trip.route_id == route_id, TripReview.is_visible == True)  # noqa: E712
    )
    agg = agg_q.one()

    # Rating distribution
    dist_q = await db.execute(
        select(TripReview.overall_rating, func.count(TripReview.id))
        .join(Trip, TripReview.trip_id == Trip.id)
        .where(Trip.route_id == route_id, TripReview.is_visible == True)  # noqa: E712
        .group_by(TripReview.overall_rating)
    )
    distribution = {i: 0 for i in range(1, 6)}
    for row in dist_q.all():
        distribution[row[0]] = row[1]

    return {
        "items": reviews, "total": total, "page": page,
        "average_rating": round(float(agg[0] or 0), 1),
        "total_reviews": agg[1],
        "rating_distribution": distribution,
    }


def _serialize_review(r: TripReview, include_user: bool = False) -> dict:
    d = {
        "id": str(r.id), "booking_id": str(r.booking_id), "trip_id": str(r.trip_id),
        "overall_rating": r.overall_rating, "driver_rating": r.driver_rating,
        "bus_condition_rating": r.bus_condition_rating, "punctuality_rating": r.punctuality_rating,
        "comfort_rating": r.comfort_rating, "comment": r.comment,
        "is_anonymous": r.is_anonymous, "admin_response": r.admin_response,
        "is_flagged": r.is_flagged, "created_at": str(r.created_at),
    }
    if include_user and hasattr(r, 'user') and r.user:
        d["reviewer_name"] = "Anonymous" if r.is_anonymous else f"{r.user.first_name} {r.user.last_name}"
    return d
