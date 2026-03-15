from app.core.constants import UserRole
from app.core.exceptions import ForbiddenError

ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.PASSENGER: 0,
    UserRole.AGENT: 1,
    UserRole.DRIVER: 2,
    UserRole.FLEET_MANAGER: 3,
    UserRole.ADMIN: 4,
    UserRole.SUPER_ADMIN: 5,
}

ADMIN_ROLES = {UserRole.ADMIN, UserRole.SUPER_ADMIN}
STAFF_ROLES = {UserRole.AGENT, UserRole.FLEET_MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN}


def check_role(user_role: UserRole, required_roles: set[UserRole]) -> None:
    if user_role not in required_roles:
        raise ForbiddenError("You do not have permission to perform this action")


def is_admin(role: UserRole) -> bool:
    return role in ADMIN_ROLES


def is_staff(role: UserRole) -> bool:
    return role in STAFF_ROLES


def has_minimum_role(user_role: UserRole, minimum_role: UserRole) -> bool:
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(minimum_role, 0)
