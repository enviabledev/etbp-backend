import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import PricingRule


async def calculate_price(
    db: AsyncSession, route_id: uuid.UUID, base_price: float
) -> float:
    result = await db.execute(
        select(PricingRule)
        .where(
            PricingRule.is_active == True,  # noqa: E712
            (PricingRule.route_id == route_id) | (PricingRule.route_id.is_(None)),
            (PricingRule.valid_from.is_(None)) | (PricingRule.valid_from <= datetime.now(timezone.utc)),
            (PricingRule.valid_until.is_(None)) | (PricingRule.valid_until >= datetime.now(timezone.utc)),
        )
        .order_by(PricingRule.priority.desc())
    )
    rules = result.scalars().all()

    final_price = base_price
    for rule in rules:
        if rule.modifier_type == "percentage":
            final_price *= 1 + (float(rule.modifier_value) / 100)
        elif rule.modifier_type == "fixed":
            final_price += float(rule.modifier_value)

    return max(final_price, 0)
