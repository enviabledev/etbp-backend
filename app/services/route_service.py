import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.route import Route, Terminal


async def get_active_routes(db: AsyncSession) -> list[Route]:
    result = await db.execute(
        select(Route)
        .options(
            selectinload(Route.origin_terminal),
            selectinload(Route.destination_terminal),
        )
        .where(Route.is_active == True)  # noqa: E712
        .order_by(Route.name)
    )
    return list(result.scalars().all())


async def get_route_by_id(db: AsyncSession, route_id: uuid.UUID) -> Route:
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
        raise NotFoundError("Route not found")
    return route
