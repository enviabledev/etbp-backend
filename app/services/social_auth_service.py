import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, create_refresh_token, hash_token
from app.models.user import RefreshToken, User

logger = logging.getLogger(__name__)


async def _generate_tokens(db: AsyncSession, user: User) -> dict:
    """Generate JWT access + refresh tokens for a user."""
    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # Store refresh token
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + __import__("datetime").timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(rt)

    user.last_login_at = datetime.now(timezone.utc)
    if not user.has_logged_in:
        user.has_logged_in = True
    await db.flush()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


async def _find_or_create_user(
    db: AsyncSession,
    *,
    provider_id_field: str,  # "google_id" or "apple_id"
    provider_id: str,
    email: str | None,
    first_name: str | None = None,
    last_name: str | None = None,
    avatar_url: str | None = None,
    email_verified: bool = False,
) -> tuple[User, bool]:
    """Find existing user or create new one. Returns (user, is_new)."""
    # 1. Look up by provider ID
    result = await db.execute(
        select(User).where(getattr(User, provider_id_field) == provider_id)
    )
    user = result.scalar_one_or_none()
    if user:
        return user, False

    # 2. Look up by email
    if email:
        result = await db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        if user:
            setattr(user, provider_id_field, provider_id)
            if avatar_url and not user.avatar_url:
                user.avatar_url = avatar_url
            if email_verified:
                user.email_verified = True
            return user, False

    # 3. Create new user
    user = User(
        email=email.lower() if email else None,
        first_name=first_name or "",
        last_name=last_name or "",
        role="passenger",
        is_active=True,
        has_logged_in=True,
        email_verified=email_verified,
        avatar_url=avatar_url,
        password_hash=None,
    )
    setattr(user, provider_id_field, provider_id)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, True


async def google_sign_in(db: AsyncSession, id_token: str) -> dict:
    """Verify Google ID token and sign in / create user."""
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        client_ids = [settings.google_client_id]
        if settings.google_client_id_ios:
            client_ids.append(settings.google_client_id_ios)

        idinfo = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), None  # audience checked manually
        )

        # Verify audience
        if idinfo.get("aud") not in client_ids:
            from app.core.exceptions import UnauthorizedError
            raise UnauthorizedError("Invalid Google token audience")

        google_user_id = idinfo["sub"]
        email = idinfo.get("email")
        name = idinfo.get("name", "")
        picture = idinfo.get("picture")
        email_verified = idinfo.get("email_verified", False)
    except ValueError as e:
        from app.core.exceptions import UnauthorizedError
        raise UnauthorizedError(f"Invalid Google ID token: {e}")

    names = name.split(" ", 1) if name else ["", ""]
    first_name = names[0]
    last_name = names[1] if len(names) > 1 else ""

    user, is_new = await _find_or_create_user(
        db,
        provider_id_field="google_id",
        provider_id=google_user_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        avatar_url=picture,
        email_verified=email_verified,
    )

    tokens = await _generate_tokens(db, user)
    tokens["is_new_user"] = is_new
    return tokens


async def apple_sign_in(
    db: AsyncSession, id_token: str,
    first_name: str | None = None, last_name: str | None = None,
) -> dict:
    """Verify Apple ID token and sign in / create user."""
    import jwt as pyjwt

    try:
        # Decode header to get kid
        header = pyjwt.get_unverified_header(id_token)
        kid = header.get("kid")

        # Fetch Apple's public keys
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://appleid.apple.com/auth/keys")
            apple_keys = resp.json()["keys"]

        # Find matching key
        key_data = next((k for k in apple_keys if k["kid"] == kid), None)
        if not key_data:
            from app.core.exceptions import UnauthorizedError
            raise UnauthorizedError("Apple key not found")

        from jwt.algorithms import RSAAlgorithm
        public_key = RSAAlgorithm.from_jwk(key_data)

        payload = pyjwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.apple_client_id,
            issuer="https://appleid.apple.com",
        )

        apple_user_id = payload["sub"]
        email = payload.get("email")
    except Exception as e:
        from app.core.exceptions import UnauthorizedError
        raise UnauthorizedError(f"Invalid Apple ID token: {e}")

    user, is_new = await _find_or_create_user(
        db,
        provider_id_field="apple_id",
        provider_id=apple_user_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        email_verified=True,  # Apple verifies email
    )

    tokens = await _generate_tokens(db, user)
    tokens["is_new_user"] = is_new
    return tokens


async def link_social_account(
    db: AsyncSession, user_id, provider: str, provider_id: str
):
    """Link a social account to an existing user."""
    from app.core.exceptions import ConflictError, BadRequestError

    field = f"{provider}_id"
    if field not in ("google_id", "apple_id"):
        raise BadRequestError("Invalid provider")

    # Check if already linked to another user
    existing = await db.execute(
        select(User).where(getattr(User, field) == provider_id)
    )
    other = existing.scalar_one_or_none()
    if other and str(other.id) != str(user_id):
        raise ConflictError(f"This {provider.title()} account is already linked to another user")

    user_q = await db.execute(select(User).where(User.id == user_id))
    user = user_q.scalar_one()
    setattr(user, field, provider_id)
    await db.flush()
    return {"linked": True, "provider": provider}
