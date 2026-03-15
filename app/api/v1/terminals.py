from fastapi import APIRouter, Query

from app.dependencies import DBSession
from app.schemas.route import TerminalBriefResponse
from app.services import route_service

router = APIRouter(prefix="/terminals", tags=["Terminals"])


@router.get("", response_model=list[TerminalBriefResponse])
async def list_terminals(
    db: DBSession,
    search: str | None = Query(None, description="Search by name, city, or code"),
):
    """List all active terminals (for autocomplete dropdowns)."""
    return await route_service.list_active_terminals(db, search=search)
