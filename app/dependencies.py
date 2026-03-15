import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_user_by_id


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid authorization header")

    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)

    if not payload or payload.get("type") != "access":
        raise UnauthorizedError("Invalid or expired access token")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    user = await get_user_by_id(db, uuid.UUID(user_id))
    if not user.is_active:
        raise UnauthorizedError("Account is deactivated")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DBSession = Annotated[AsyncSession, Depends(get_db)]


def require_role(*roles: UserRole):
    async def role_checker(current_user: CurrentUser) -> User:
        if UserRole(current_user.role) not in roles:
            raise ForbiddenError("You do not have permission to perform this action")
        return current_user
    return role_checker
