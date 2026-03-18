import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.models.banner import Banner
from app.services.audit_service import log_action

router = APIRouter(prefix="/banners", tags=["Admin - Banners"])
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateBannerRequest(BaseModel):
    title: str = Field(..., max_length=200)
    heading: str | None = None
    body_text: str | None = None
    image_url: str | None = None
    background_color: str | None = None
    text_color: str | None = None
    cta_text: str | None = None
    cta_action: str | None = None
    cta_value: str | None = None
    placement: str = "home_hero"
    target_audience: str = "all"
    start_date: datetime
    end_date: datetime
    priority: int = 0
    is_active: bool = True


@router.get("", dependencies=[AdminUser])
async def list_banners(db: DBSession, placement: str | None = None, is_active: bool | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(Banner)
    if placement:
        query = query.where(Banner.placement == placement)
    if is_active is not None:
        query = query.where(Banner.is_active == is_active)
    count_q = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_q.scalar() or 0
    query = query.order_by(Banner.priority.desc(), Banner.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    now = datetime.now(timezone.utc)
    items = []
    for b in result.scalars().all():
        status = "active" if b.is_active and b.start_date <= now <= b.end_date else "scheduled" if b.start_date > now else "expired"
        ctr = round(b.clicks / max(b.impressions, 1) * 100, 1)
        items.append({
            "id": str(b.id), "title": b.title, "heading": b.heading, "placement": b.placement,
            "start_date": str(b.start_date), "end_date": str(b.end_date),
            "status": status, "is_active": b.is_active, "impressions": b.impressions,
            "clicks": b.clicks, "ctr": ctr, "priority": b.priority, "created_at": str(b.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_banner(data: CreateBannerRequest, db: DBSession, current_user: CurrentUser):
    banner = Banner(**data.model_dump(), created_by=current_user.id)
    db.add(banner)
    await db.flush()
    await log_action(db, current_user.id, "create_banner", "banner", str(banner.id))
    return {"id": str(banner.id), "title": banner.title}


@router.put("/{banner_id}", dependencies=[AdminUser])
async def update_banner(banner_id: uuid.UUID, data: CreateBannerRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Banner).where(Banner.id == banner_id))
    banner = result.scalar_one_or_none()
    if not banner:
        raise NotFoundError("Banner not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(banner, field, value)
    await db.flush()
    return {"id": str(banner.id), "updated": True}


@router.delete("/{banner_id}", dependencies=[AdminUser])
async def delete_banner(banner_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Banner).where(Banner.id == banner_id))
    banner = result.scalar_one_or_none()
    if not banner:
        raise NotFoundError("Banner not found")
    await db.delete(banner)
    await db.flush()
    return {"deleted": True}
