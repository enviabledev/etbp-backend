import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import DBSession
from app.models.route import Route, Terminal
from app.schemas.route import RouteDetailResponse, RouteResponse, TerminalResponse

router = APIRouter(prefix="/routes", tags=["Routes"])


@router.get("/terminals", response_model=list[TerminalResponse])
async def list_terminals(
    db: DBSession,
    city: str | None = None,
    state: str | None = None,
    is_active: bool = True,
):
    query = select(Terminal).where(Terminal.is_active == is_active)
    if city:
        query = query.where(Terminal.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Terminal.state.ilike(f"%{state}%"))
    result = await db.execute(query.order_by(Terminal.name))
    return result.scalars().all()


@router.get("", response_model=list[RouteResponse])
async def list_routes(
    db: DBSession,
    origin: str | None = Query(None, description="Origin city or terminal code"),
    destination: str | None = Query(None, description="Destination city or terminal code"),
    is_active: bool = True,
):
    query = (
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
        )
        .where(Route.is_active == is_active)
    )
    if origin:
        query = query.join(
            Terminal, Route.origin_terminal_id == Terminal.id
        ).where(
            (Terminal.city.ilike(f"%{origin}%")) | (Terminal.code == origin.upper())
        )
    if destination:
        dest_terminal = Terminal.__table__.alias("dest_t")
        query = query.where(
            Route.destination_terminal_id.in_(
                select(Terminal.id).where(
                    (Terminal.city.ilike(f"%{destination}%"))
                    | (Terminal.code == destination.upper())
                )
            )
        )
    result = await db.execute(query.order_by(Route.name))
    return result.scalars().all()


@router.get("/{route_id}", response_model=RouteDetailResponse)
async def get_route(route_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
            selectinload(Route.stops),
        )
        .where(Route.id == route_id)
    )
    route = result.scalar_one_or_none()
    if not route:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Route not found")
    return route
