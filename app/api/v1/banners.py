import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select, update

from app.dependencies import DBSession
from app.models.banner import Banner

router = APIRouter(prefix="/banners", tags=["Banners"])


@router.get("")
async def list_banners(db: DBSession, placement: str | None = None):
    now = datetime.now(timezone.utc)
    query = select(Banner).where(
        Banner.is_active == True,  # noqa: E712
        Banner.start_date <= now,
        Banner.end_date >= now,
    )
    if placement:
        placements = placement.split(",")
        query = query.where(Banner.placement.in_(placements))
    query = query.order_by(Banner.priority.desc())
    result = await db.execute(query)
    banners = result.scalars().all()

    # Increment impressions
    ids = [b.id for b in banners]
    if ids:
        await db.execute(update(Banner).where(Banner.id.in_(ids)).values(impressions=Banner.impressions + 1))
        await db.flush()

    return [
        {
            "id": str(b.id), "heading": b.heading, "body_text": b.body_text,
            "image_url": b.image_url, "background_color": b.background_color,
            "text_color": b.text_color, "cta_text": b.cta_text,
            "cta_action": b.cta_action, "cta_value": b.cta_value, "placement": b.placement,
        }
        for b in banners
    ]


@router.post("/{banner_id}/click")
async def track_click(banner_id: uuid.UUID, db: DBSession):
    await db.execute(update(Banner).where(Banner.id == banner_id).values(clicks=Banner.clicks + 1))
    await db.flush()
    return {"tracked": True}
