import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.constants import UserRole
from app.core.exceptions import NotFoundError
from app.dependencies import DBSession, require_role
from app.models.route import Route, Terminal

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


class CreateRouteRequest(BaseModel):
    name: str = Field(..., max_length=255)
    code: str = Field(..., max_length=20)
    origin_terminal_id: uuid.UUID
    destination_terminal_id: uuid.UUID
    distance_km: float | None = None
    estimated_duration_minutes: int | None = None
    base_price: float
    luggage_policy: str | None = None


@router.post("/terminals", status_code=201, dependencies=[AdminUser])
async def create_terminal(data: CreateTerminalRequest, db: DBSession):
    terminal = Terminal(**data.model_dump())
    db.add(terminal)
    await db.flush()
    await db.refresh(terminal)
    return terminal


@router.post("", status_code=201, dependencies=[AdminUser])
async def create_route(data: CreateRouteRequest, db: DBSession):
    route = Route(**data.model_dump())
    db.add(route)
    await db.flush()
    await db.refresh(route)
    return route


@router.put("/{route_id}", dependencies=[AdminUser])
async def update_route(route_id: uuid.UUID, data: dict, db: DBSession):
    result = await db.execute(select(Route).where(Route.id == route_id))
    route = result.scalar_one_or_none()
    if not route:
        raise NotFoundError("Route not found")
    for key, value in data.items():
        if hasattr(route, key):
            setattr(route, key, value)
    await db.flush()
    await db.refresh(route)
    return route
