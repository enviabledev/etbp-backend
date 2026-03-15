import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import PricingModifierType, PricingRuleType, UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.notification import AuditLog, PricingRule, SupportTicket

router = APIRouter(prefix="/settings", tags=["Admin - Settings"])

SuperAdmin = Depends(require_role(UserRole.SUPER_ADMIN))
AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


# ── Audit Logs ──


@router.get("/audit-logs", dependencies=[SuperAdmin])
async def list_audit_logs(
    db: DBSession,
    action: str | None = None,
    resource_type: str | None = None,
    user_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if from_date:
        query = query.where(func.date(AuditLog.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(AuditLog.created_at) <= to_date)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(AuditLog.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


# ── Support Tickets ──


@router.get("/support-tickets", dependencies=[AdminUser])
async def list_support_tickets(
    db: DBSession,
    status: str | None = None,
    priority: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(SupportTicket)
    if status:
        query = query.where(SupportTicket.status == status)
    if priority:
        query = query.where(SupportTicket.priority == priority)
    if category:
        query = query.where(SupportTicket.category == category)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar()

    query = query.order_by(SupportTicket.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.put("/support-tickets/{ticket_id}/assign", dependencies=[AdminUser])
async def assign_ticket(ticket_id: uuid.UUID, assignee_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundError("Ticket not found")
    ticket.assigned_to = assignee_id
    ticket.status = "in_progress"
    await db.flush()
    return {"id": str(ticket.id), "assigned_to": str(assignee_id), "status": ticket.status}


@router.put("/support-tickets/{ticket_id}/resolve", dependencies=[AdminUser])
async def resolve_ticket(ticket_id: uuid.UUID, db: DBSession):
    from datetime import datetime, timezone
    result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise NotFoundError("Ticket not found")
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return {"id": str(ticket.id), "status": ticket.status, "resolved_at": str(ticket.resolved_at)}


# ── Pricing Rules ──


class CreatePricingRuleRequest(BaseModel):
    name: str = Field(..., max_length=255)
    route_id: uuid.UUID | None = None
    rule_type: PricingRuleType
    condition: dict | None = None
    modifier_type: PricingModifierType
    modifier_value: float
    priority: int = 0
    valid_from: str | None = None
    valid_until: str | None = None


@router.get("/pricing-rules", dependencies=[AdminUser])
async def list_pricing_rules(
    db: DBSession,
    route_id: uuid.UUID | None = None,
    is_active: bool | None = None,
):
    query = select(PricingRule)
    if route_id:
        query = query.where(PricingRule.route_id == route_id)
    if is_active is not None:
        query = query.where(PricingRule.is_active == is_active)
    query = query.order_by(PricingRule.priority.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/pricing-rules", status_code=201, dependencies=[AdminUser])
async def create_pricing_rule(data: CreatePricingRuleRequest, db: DBSession):
    rule = PricingRule(
        name=data.name,
        route_id=data.route_id,
        rule_type=data.rule_type.value,
        condition=data.condition,
        modifier_type=data.modifier_type.value,
        modifier_value=data.modifier_value,
        priority=data.priority,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.put("/pricing-rules/{rule_id}/deactivate", dependencies=[AdminUser])
async def deactivate_pricing_rule(rule_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(PricingRule).where(PricingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("Pricing rule not found")
    rule.is_active = False
    await db.flush()
    return {"id": str(rule.id), "is_active": False}
