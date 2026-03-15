"""Create initial superadmin user."""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import UserRole
from app.core.security import hash_password
from app.database import async_session_factory
from app.models.user import User


async def create_superadmin():
    async with async_session_factory() as db:
        result = await db.execute(
            select(User).where(User.email == settings.superadmin_email)
        )
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Superadmin {settings.superadmin_email} already exists.")
            return

        user = User(
            email=settings.superadmin_email,
            password_hash=hash_password(settings.superadmin_password),
            first_name="Super",
            last_name="Admin",
            role=UserRole.SUPER_ADMIN,
            email_verified=True,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print(f"Superadmin created: {settings.superadmin_email}")


if __name__ == "__main__":
    asyncio.run(create_superadmin())
