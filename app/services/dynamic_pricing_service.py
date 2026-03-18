import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import PricingRule

logger = logging.getLogger(__name__)


async def get_active_rules(db: AsyncSession) -> list[PricingRule]:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PricingRule).where(
            PricingRule.is_active == True,  # noqa: E712
        ).order_by(PricingRule.priority.desc())
    )
    rules = []
    for r in result.scalars().all():
        if r.valid_from and r.valid_from > now:
            continue
        if r.valid_until and r.valid_until < now:
            continue
        rules.append(r)
    return rules


def rule_applies(rule: PricingRule, trip, booking_date: datetime) -> bool:
    cond = rule.condition or {}

    # Route filter
    if rule.route_id and str(rule.route_id) != str(trip.route_id):
        return False
    routes = cond.get("routes")
    if routes and str(trip.route_id) not in [str(r) for r in routes]:
        return False

    # Day of week
    days = cond.get("days_of_week")
    if days is not None:
        dep_day = trip.departure_date.weekday() if hasattr(trip.departure_date, 'weekday') else date.today().weekday()
        if dep_day not in days:
            return False

    # Time ranges
    time_ranges = cond.get("time_ranges")
    if time_ranges and hasattr(trip, 'departure_time') and trip.departure_time:
        t = trip.departure_time
        time_str = f"{t.hour:02d}:{t.minute:02d}" if hasattr(t, 'hour') else str(t)[:5]
        in_range = False
        for tr in time_ranges:
            if tr.get("start", "00:00") <= time_str <= tr.get("end", "23:59"):
                in_range = True
                break
        if not in_range:
            return False

    # Date ranges
    date_ranges = cond.get("date_ranges")
    if date_ranges:
        dep_str = str(trip.departure_date)
        in_range = False
        for dr in date_ranges:
            if dr.get("start", "") <= dep_str <= dr.get("end", "9999-12-31"):
                in_range = True
                break
        if not in_range:
            return False

    # Advance booking (days before departure)
    min_days = cond.get("min_days_before")
    if min_days is not None:
        days_before = (trip.departure_date - booking_date.date()).days if hasattr(trip.departure_date, 'year') else 0
        if days_before < min_days:
            return False

    max_days = cond.get("max_days_before")
    if max_days is not None:
        days_before = (trip.departure_date - booking_date.date()).days if hasattr(trip.departure_date, 'year') else 0
        if days_before > max_days:
            return False

    # Occupancy
    min_occ = cond.get("min_occupancy")
    if min_occ is not None and hasattr(trip, 'total_seats') and trip.total_seats > 0:
        occ = 1.0 - (trip.available_seats / trip.total_seats)
        if occ < min_occ:
            return False

    max_occ = cond.get("max_occupancy")
    if max_occ is not None and hasattr(trip, 'total_seats') and trip.total_seats > 0:
        occ = 1.0 - (trip.available_seats / trip.total_seats)
        if occ > max_occ:
            return False

    return True


async def calculate_trip_price(db: AsyncSession, trip, base_price: float, booking_date: datetime | None = None) -> dict:
    booking_date = booking_date or datetime.now(timezone.utc)
    rules = await get_active_rules(db)

    applied = []
    final_price = base_price
    for rule in rules:
        if rule_applies(rule, trip, booking_date):
            if rule.modifier_type == "percentage":
                change = base_price * (float(rule.modifier_value) / 100)
            else:
                change = float(rule.modifier_value)
            final_price += change
            applied.append({
                "rule_name": rule.name,
                "rule_type": rule.rule_type,
                "change": round(change, 2),
            })

    final_price = max(final_price, base_price * 0.2)

    return {
        "base_price": round(base_price, 2),
        "final_price": round(final_price, 2),
        "adjustments": applied,
        "rules_applied": len(applied),
    }
