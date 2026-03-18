import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError
from app.dependencies import CurrentUser, DBSession
from app.models.payment import PromoCode
from app.models.promo_usage import PromoUsage
from app.models.schedule import Trip

router = APIRouter(prefix="/promo", tags=["Promo Codes"])


class ValidatePromoRequest(BaseModel):
    code: str
    trip_id: uuid.UUID | None = None
    amount: float


@router.post("/validate")
async def validate_promo(data: ValidatePromoRequest, db: DBSession, current_user: CurrentUser):
    code = data.code.strip().upper()
    result = await db.execute(select(PromoCode).where(PromoCode.code == code))
    promo = result.scalar_one_or_none()

    if not promo:
        return {"valid": False, "reason": "not_found"}
    if not promo.is_active:
        return {"valid": False, "reason": "inactive"}

    now = datetime.now(timezone.utc)
    if promo.valid_from and promo.valid_from > now:
        return {"valid": False, "reason": "not_yet_active"}
    if promo.valid_until and promo.valid_until < now:
        return {"valid": False, "reason": "expired"}
    if promo.usage_limit and promo.used_count >= promo.usage_limit:
        return {"valid": False, "reason": "usage_limit_reached"}

    # Per-user limit
    if promo.per_user_limit:
        usage_count = await db.execute(
            select(func.count(PromoUsage.id)).where(
                PromoUsage.promo_id == promo.id,
                PromoUsage.user_id == current_user.id,
            )
        )
        if (usage_count.scalar() or 0) >= promo.per_user_limit:
            return {"valid": False, "reason": "already_used"}

    if promo.min_booking_amount and data.amount < float(promo.min_booking_amount):
        return {"valid": False, "reason": "min_amount_not_met"}

    # Route check
    if promo.applicable_routes and data.trip_id:
        route_ids = promo.applicable_routes.get("route_ids", [])
        if route_ids:
            trip_q = await db.execute(select(Trip.route_id).where(Trip.id == data.trip_id))
            trip_route = trip_q.scalar_one_or_none()
            if trip_route and str(trip_route) not in [str(r) for r in route_ids]:
                return {"valid": False, "reason": "not_applicable_route"}

    # Calculate discount
    amount = data.amount
    if promo.discount_type == "percentage":
        discount = amount * float(promo.discount_value) / 100
        if promo.max_discount:
            discount = min(discount, float(promo.max_discount))
    else:
        discount = float(promo.discount_value)
    discount = min(discount, amount)
    discount = round(discount, 2)

    return {
        "valid": True,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": float(promo.discount_value),
        "discount_amount": discount,
        "final_amount": round(amount - discount, 2),
        "message": f"{'%.0f' % promo.discount_value}% off applied!" if promo.discount_type == "percentage" else f"\u20a6{discount:,.0f} off applied!",
    }
