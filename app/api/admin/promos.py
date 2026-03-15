import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.constants import DiscountType, UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import DBSession, require_role
from app.models.payment import PromoCode

router = APIRouter(prefix="/promos", tags=["Admin - Promo Codes"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreatePromoRequest(BaseModel):
    code: str = Field(..., max_length=50)
    description: str | None = None
    discount_type: DiscountType
    discount_value: float
    max_discount: float | None = None
    min_booking_amount: float | None = None
    usage_limit: int | None = None
    per_user_limit: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    applicable_routes: dict | None = None


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_promo(data: CreatePromoRequest, db: DBSession):
    existing = await db.execute(
        select(PromoCode).where(PromoCode.code == data.code.upper())
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Promo code already exists")

    promo = PromoCode(**data.model_dump())
    promo.code = promo.code.upper()
    db.add(promo)
    await db.flush()
    await db.refresh(promo)
    return promo


@router.get("", dependencies=[AdminUser])
async def list_promos(
    db: DBSession,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(PromoCode)
    if is_active is not None:
        query = query.where(PromoCode.is_active == is_active)
    query = query.order_by(PromoCode.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/{promo_id}/deactivate", dependencies=[AdminUser])
async def deactivate_promo(promo_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError("Promo code not found")
    promo.is_active = False
    await db.flush()
    return {"id": str(promo.id), "is_active": promo.is_active}
