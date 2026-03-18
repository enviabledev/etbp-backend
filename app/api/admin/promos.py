import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import DiscountType, UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.services.audit_service import log_action
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


class UpdatePromoRequest(BaseModel):
    description: str | None = None
    discount_value: float | None = None
    max_discount: float | None = None
    min_booking_amount: float | None = None
    usage_limit: int | None = None
    per_user_limit: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    applicable_routes: dict | None = None
    is_active: bool | None = None


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_promo(data: CreatePromoRequest, db: DBSession, current_user: CurrentUser):
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
    await log_action(db, current_user.id, "create_promo", "promo", str(promo.id), {"code": promo.code})
    return promo


@router.get("", dependencies=[AdminUser])
async def list_promos(
    db: DBSession,
    is_active: bool | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(PromoCode)
    if is_active is not None:
        query = query.where(PromoCode.is_active == is_active)
    if search:
        query = query.where(
            PromoCode.code.ilike(f"%{search.upper()}%")
            | PromoCode.description.ilike(f"%{search}%")
        )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(PromoCode.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/{promo_id}", dependencies=[AdminUser])
async def get_promo(promo_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError("Promo code not found")

    remaining = None
    if promo.usage_limit:
        remaining = promo.usage_limit - promo.used_count

    return {
        "promo": promo,
        "usage_remaining": remaining,
    }


@router.put("/{promo_id}", dependencies=[AdminUser])
async def update_promo(promo_id: uuid.UUID, data: UpdatePromoRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError("Promo code not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(promo, field, value)
    await db.flush()
    await db.refresh(promo)
    await log_action(db, current_user.id, "update_promo", "promo", str(promo_id))
    return promo


@router.put("/{promo_id}/deactivate", dependencies=[AdminUser])
async def deactivate_promo(promo_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError("Promo code not found")
    promo.is_active = False
    await db.flush()
    await log_action(db, current_user.id, "deactivate_promo", "promo", str(promo_id))
    return {"id": str(promo.id), "code": promo.code, "is_active": promo.is_active}


@router.get("/{promo_id}/usage", dependencies=[AdminUser])
async def get_promo_usage(promo_id: uuid.UUID, db: DBSession):
    from app.models.promo_usage import PromoUsage
    from app.models.user import User

    result = await db.execute(
        select(PromoUsage, User.first_name, User.last_name, User.email)
        .join(User, PromoUsage.user_id == User.id)
        .where(PromoUsage.promo_id == promo_id)
        .order_by(PromoUsage.used_at.desc())
        .limit(50)
    )
    items = []
    for row in result.all():
        usage = row[0]
        items.append({
            "id": str(usage.id),
            "user_name": f"{row[1]} {row[2]}",
            "user_email": row[3],
            "discount_applied": float(usage.discount_applied),
            "booking_id": str(usage.booking_id) if usage.booking_id else None,
            "used_at": str(usage.used_at),
        })

    # Total discount given
    total_q = await db.execute(
        select(func.sum(PromoUsage.discount_applied)).where(PromoUsage.promo_id == promo_id)
    )
    total_discount = float(total_q.scalar() or 0)

    return {"items": items, "total_discount": total_discount, "usage_count": len(items)}
