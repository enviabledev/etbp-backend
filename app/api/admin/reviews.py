import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.review import TripReview
from app.models.schedule import Trip
from app.models.route import Route
from app.models.user import User
from app.services.audit_service import log_action

router = APIRouter(prefix="/reviews", tags=["Admin - Reviews"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


@router.get("", dependencies=[AdminUser])
async def list_reviews(
    db: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    min_rating: int | None = None,
    max_rating: int | None = None,
    flagged_only: bool = False,
    driver_id: uuid.UUID | None = None,
):
    query = select(TripReview).options(selectinload(TripReview.user))
    if min_rating:
        query = query.where(TripReview.overall_rating >= min_rating)
    if max_rating:
        query = query.where(TripReview.overall_rating <= max_rating)
    if flagged_only:
        query = query.where(TripReview.is_flagged == True)  # noqa: E712
    if driver_id:
        query = query.where(TripReview.driver_id == driver_id)

    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0

    query = query.order_by(TripReview.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = []
    for r in result.scalars().all():
        items.append({
            "id": str(r.id), "booking_id": str(r.booking_id),
            "overall_rating": r.overall_rating, "driver_rating": r.driver_rating,
            "comment": (r.comment or "")[:100], "is_flagged": r.is_flagged, "is_visible": r.is_visible,
            "has_response": r.admin_response is not None,
            "reviewer": f"{r.user.first_name} {r.user.last_name}" if r.user else "Unknown",
            "created_at": str(r.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{review_id}", dependencies=[AdminUser])
async def get_review(review_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(TripReview).options(selectinload(TripReview.user)).where(TripReview.id == review_id)
    )
    review = result.scalar_one_or_none()
    if not review:
        raise NotFoundError("Review not found")
    return {
        "id": str(review.id), "booking_id": str(review.booking_id), "trip_id": str(review.trip_id),
        "overall_rating": review.overall_rating, "driver_rating": review.driver_rating,
        "bus_condition_rating": review.bus_condition_rating, "punctuality_rating": review.punctuality_rating,
        "comfort_rating": review.comfort_rating, "comment": review.comment,
        "is_anonymous": review.is_anonymous, "is_flagged": review.is_flagged, "is_visible": review.is_visible,
        "admin_response": review.admin_response, "admin_responded_at": str(review.admin_responded_at) if review.admin_responded_at else None,
        "reviewer": f"{review.user.first_name} {review.user.last_name}" if review.user else "Unknown",
        "created_at": str(review.created_at),
    }


class RespondRequest(BaseModel):
    response: str


@router.post("/{review_id}/respond", dependencies=[AdminUser])
async def respond_to_review(review_id: uuid.UUID, data: RespondRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(TripReview).where(TripReview.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise NotFoundError("Review not found")
    review.admin_response = data.response
    review.admin_responded_at = datetime.now(timezone.utc)
    review.admin_responded_by = current_user.id
    await db.flush()
    await log_action(db, current_user.id, "respond_review", "review", str(review_id))

    try:
        from app.services.push_notification_service import send_push_to_user
        await send_push_to_user(db, review.user_id, "Response to your review", "Enviable Transport responded to your feedback.", {"type": "review_response", "review_id": str(review_id)}, "customer")
    except Exception:
        pass

    return {"message": "Response added"}


@router.post("/{review_id}/flag", dependencies=[AdminUser])
async def flag_review(review_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(TripReview).where(TripReview.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise NotFoundError("Review not found")
    review.is_flagged = True
    review.is_visible = False
    await db.flush()
    await log_action(db, current_user.id, "flag_review", "review", str(review_id))
    return {"flagged": True}


@router.post("/{review_id}/unflag", dependencies=[AdminUser])
async def unflag_review(review_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(TripReview).where(TripReview.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise NotFoundError("Review not found")
    review.is_flagged = False
    review.is_visible = True
    await db.flush()
    return {"flagged": False}
