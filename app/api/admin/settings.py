from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.notification import AuditLog

router = APIRouter(prefix="/settings", tags=["Admin - Settings"])

SuperAdmin = Depends(require_role(UserRole.SUPER_ADMIN))


@router.get("/audit-logs", dependencies=[SuperAdmin])
async def list_audit_logs(
    db: DBSession,
    action: str | None = None,
    resource_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    query = query.order_by(AuditLog.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()
