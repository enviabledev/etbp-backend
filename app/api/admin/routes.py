import uuid
from datetime import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.constants import UserRole
from app.core.exceptions import ConflictError, NotFoundError
from app.dependencies import CurrentUser, DBSession, require_role
from app.services.audit_service import log_action
from app.models.route import Route, RouteStop, Terminal

router = APIRouter(prefix="/routes", tags=["Admin - Routes"])

AdminUser = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


class CreateTerminalRequest(BaseModel):
    name: str = Field(..., max_length=255)
    code: str = Field(..., max_length=20)
    city: str = Field(..., max_length=100)
    state: str = Field(..., max_length=100)
    country: str = "Nigeria"
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    amenities: dict | None = None
    opening_time: time | None = None
    closing_time: time | None = None


class UpdateTerminalRequest(BaseModel):
    name: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    amenities: dict | None = None
    is_active: bool | None = None
    opening_time: time | None = None
    closing_time: time | None = None


class CreateRouteRequest(BaseModel):
    name: str = Field(..., max_length=255)
    code: str = Field(..., max_length=20)
    origin_terminal_id: uuid.UUID
    destination_terminal_id: uuid.UUID
    distance_km: float | None = None
    estimated_duration_minutes: int | None = None
    base_price: float
    luggage_policy: str | None = None


class UpdateRouteRequest(BaseModel):
    name: str | None = None
    distance_km: float | None = None
    estimated_duration_minutes: int | None = None
    base_price: float | None = None
    luggage_policy: str | None = None
    is_active: bool | None = None


class AddRouteStopRequest(BaseModel):
    terminal_id: uuid.UUID
    stop_order: int
    duration_from_origin_minutes: int | None = None
    price_from_origin: float | None = None
    is_pickup_point: bool = True
    is_dropoff_point: bool = True


# ── Terminals ──


@router.post("/terminals", status_code=201, dependencies=[AdminUser])
async def create_terminal(data: CreateTerminalRequest, db: DBSession, current_user: CurrentUser):
    existing = await db.execute(
        select(Terminal).where(Terminal.code == data.code.upper())
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Terminal code already exists")

    terminal = Terminal(**data.model_dump())
    terminal.code = terminal.code.upper()
    db.add(terminal)
    await db.flush()
    await db.refresh(terminal)
    await log_action(db, current_user.id, "create_terminal", "terminal", str(terminal.id), {"name": terminal.name, "code": terminal.code})
    return terminal


@router.get("/terminals", dependencies=[AdminUser])
async def list_terminals(
    db: DBSession,
    search: str | None = None,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    query = select(Terminal)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Terminal.name.ilike(pattern)
            | Terminal.city.ilike(pattern)
            | Terminal.code.ilike(pattern)
        )
    if is_active is not None:
        query = query.where(Terminal.is_active == is_active)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar()

    query = query.order_by(Terminal.city, Terminal.name).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/terminals/{terminal_id}", dependencies=[AdminUser])
async def get_terminal(terminal_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Terminal).where(Terminal.id == terminal_id))
    terminal = result.scalar_one_or_none()
    if not terminal:
        raise NotFoundError("Terminal not found")

    # Get agents assigned to this terminal
    from app.models.user import User
    agents_q = await db.execute(
        select(User).where(User.assigned_terminal_id == terminal_id, User.role == "agent", User.is_active == True)  # noqa: E712
    )
    agents = [{"id": str(a.id), "first_name": a.first_name, "last_name": a.last_name, "email": a.email, "phone": a.phone} for a in agents_q.scalars().all()]

    # Return terminal data + agents
    return {
        **{c.name: getattr(terminal, c.name) for c in terminal.__table__.columns},
        "id": str(terminal.id),
        "assigned_agents": agents,
    }


@router.put("/terminals/{terminal_id}", dependencies=[AdminUser])
async def update_terminal(terminal_id: uuid.UUID, data: UpdateTerminalRequest, db: DBSession):
    result = await db.execute(select(Terminal).where(Terminal.id == terminal_id))
    terminal = result.scalar_one_or_none()
    if not terminal:
        raise NotFoundError("Terminal not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(terminal, field, value)
    await db.flush()
    await db.refresh(terminal)
    return terminal


# ── Routes ──


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_route(data: CreateRouteRequest, db: DBSession, current_user: CurrentUser):
    existing = await db.execute(
        select(Route).where(Route.code == data.code.upper())
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Route code already exists")

    route = Route(**data.model_dump())
    route.code = route.code.upper()
    db.add(route)
    await db.flush()
    await db.refresh(route)
    await log_action(db, current_user.id, "create_route", "route", str(route.id), {"name": route.name})
    return route


@router.get("", dependencies=[AdminUser])
async def list_routes(
    db: DBSession,
    search: str | None = None,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(Route).options(
        selectinload(Route.origin_terminal),
        selectinload(Route.destination_terminal),
    )
    if search:
        pattern = f"%{search}%"
        query = query.where(Route.name.ilike(pattern) | Route.code.ilike(pattern))
    if is_active is not None:
        query = query.where(Route.is_active == is_active)

    count_result = await db.execute(
        select(func.count()).select_from(
            select(Route.id).where(
                *([Route.name.ilike(f"%{search}%") | Route.code.ilike(f"%{search}%")] if search else []),
                *([Route.is_active == is_active] if is_active is not None else []),
            ).subquery()
        )
    )
    total = count_result.scalar()

    query = query.order_by(Route.name).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(query)
    return {"items": result.scalars().all(), "total": total, "page": page, "page_size": page_size}


@router.get("/{route_id}", dependencies=[AdminUser])
async def get_route(route_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
            selectinload(Route.stops).selectinload(RouteStop.terminal),
        )
        .where(Route.id == route_id)
    )
    route = result.scalar_one_or_none()
    if not route:
        raise NotFoundError("Route not found")
    return route


@router.put("/{route_id}", dependencies=[AdminUser])
async def update_route(route_id: uuid.UUID, data: UpdateRouteRequest, db: DBSession, current_user: CurrentUser):
    result = await db.execute(select(Route).where(Route.id == route_id))
    route = result.scalar_one_or_none()
    if not route:
        raise NotFoundError("Route not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(route, field, value)
    await db.flush()
    await db.refresh(route)
    await log_action(db, current_user.id, "update_route", "route", str(route_id))
    return route


@router.get("/{route_id}/stops", dependencies=[AdminUser])
async def list_route_stops(route_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(RouteStop).where(RouteStop.route_id == route_id).order_by(RouteStop.stop_order)
    )
    stops = result.scalars().all()
    return [
        {
            "id": str(s.id), "route_id": str(s.route_id), "terminal_id": str(s.terminal_id) if s.terminal_id else None,
            "name": getattr(s, "name", None) or f"Stop {s.stop_order}",
            "city": getattr(s, "city", None),
            "latitude": getattr(s, "latitude", None),
            "longitude": getattr(s, "longitude", None),
            "stop_order": s.stop_order,
            "duration_from_origin_minutes": s.duration_from_origin_minutes,
            "stop_duration_minutes": getattr(s, "stop_duration_minutes", 0),
            "is_pickup_point": s.is_pickup_point,
            "is_dropoff_point": s.is_dropoff_point,
            "is_rest_stop": getattr(s, "is_rest_stop", True),
            "notes": getattr(s, "notes", None),
        }
        for s in stops
    ]


@router.put("/route-stops/{stop_id}", dependencies=[AdminUser])
async def update_route_stop(stop_id: uuid.UUID, data: AddRouteStopRequest, db: DBSession):
    result = await db.execute(select(RouteStop).where(RouteStop.id == stop_id))
    stop = result.scalar_one_or_none()
    if not stop:
        raise NotFoundError("Route stop not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(stop, field, value)
    await db.flush()
    await db.refresh(stop)
    return stop


@router.post("/{route_id}/stops", status_code=201, dependencies=[AdminUser])
async def add_route_stop(route_id: uuid.UUID, data: AddRouteStopRequest, db: DBSession):
    route_result = await db.execute(select(Route).where(Route.id == route_id))
    if not route_result.scalar_one_or_none():
        raise NotFoundError("Route not found")

    stop = RouteStop(route_id=route_id, **data.model_dump())
    db.add(stop)
    await db.flush()
    await db.refresh(stop)
    return stop


@router.delete("/{route_id}/stops/{stop_id}", dependencies=[AdminUser])
async def remove_route_stop(route_id: uuid.UUID, stop_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(RouteStop).where(RouteStop.id == stop_id, RouteStop.route_id == route_id)
    )
    stop = result.scalar_one_or_none()
    if not stop:
        raise NotFoundError("Route stop not found")
    await db.delete(stop)
    await db.flush()
    return {"message": "Route stop removed"}


class ReorderStopsRequest(BaseModel):
    stop_ids: list[uuid.UUID]


@router.post("/{route_id}/stops/reorder", status_code=200, dependencies=[AdminUser])
async def reorder_stops(route_id: uuid.UUID, data: ReorderStopsRequest, db: DBSession):
    for i, stop_id in enumerate(data.stop_ids):
        result = await db.execute(
            select(RouteStop).where(RouteStop.id == stop_id, RouteStop.route_id == route_id)
        )
        stop = result.scalar_one_or_none()
        if stop:
            stop.stop_order = i + 1
    await db.flush()
    return {"reordered": True}
