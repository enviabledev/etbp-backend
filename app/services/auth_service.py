import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserResponse, UserUpdateRequest


async def register_user(db: AsyncSession, data: RegisterRequest) -> TokenResponse:
    from sqlalchemy import or_

    # Check for existing user (might be admin-created walk-in customer)
    conditions = [User.email == data.email.lower()]
    if data.phone:
        conditions.append(User.phone == data.phone)

    existing_q = await db.execute(
        select(User).where(or_(*conditions))
    )
    existing_user = existing_q.scalar_one_or_none()

    if existing_user:
        if existing_user.has_logged_in:
            # Truly existing user — reject
            raise ConflictError("An account with this email or phone already exists. Please login instead.")

        # Admin-created user who never logged in — let them claim the account
        existing_user.password_hash = hash_password(data.password)
        existing_user.has_logged_in = True
        existing_user.email = data.email.lower()
        if data.first_name:
            existing_user.first_name = data.first_name
        if data.last_name:
            existing_user.last_name = data.last_name
        if data.phone:
            existing_user.phone = data.phone
        await db.flush()

        token_data = {"sub": str(existing_user.id), "role": existing_user.role}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        rt = RefreshToken(
            user_id=existing_user.id,
            token_hash=hash_token(refresh_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        )
        db.add(rt)
        await db.flush()

        return TokenResponse(access_token=access_token, refresh_token=refresh_token)

    if data.phone:

        # Verify phone was confirmed via OTP (skip in dev if no Redis)
        try:
            import redis.asyncio as _redis
            r = _redis.from_url(settings.redis_url)
            verified = await r.get(f"phone_verified:{data.phone}")
            await r.aclose()
            if not verified:
                raise BadRequestError("Phone number must be verified via OTP before registration.")
            # Clean up verification key
            r = _redis.from_url(settings.redis_url)
            await r.delete(f"phone_verified:{data.phone}")
            await r.aclose()
        except (ConnectionError, OSError):
            pass  # Redis not available — skip check in dev

    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        role=UserRole.PASSENGER,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Send welcome email via Celery
    if user.email:
        from app.tasks.email_tasks import send_welcome_email
        send_welcome_email.delay(user.email, data.first_name)

    # Create tokens (auto-login after registration)
    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(rt)
    await db.flush()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def login_user(db: AsyncSession, data: LoginRequest) -> TokenResponse:
    result = await db.execute(
        select(User).where(User.email == data.email.lower())
    )
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")

    if not user.is_active:
        raise UnauthorizedError("Account is deactivated")

    # Update last login and mark as logged in
    user.last_login_at = datetime.now(timezone.utc)
    if not user.has_logged_in:
        user.has_logged_in = True

    # Create tokens
    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # Store refresh token hash
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(rt)
    await db.flush()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> TokenResponse:
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise UnauthorizedError("Invalid refresh token")

    token_hash = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,  # noqa: E712
        )
    )
    stored_token = result.scalar_one_or_none()
    if not stored_token:
        raise UnauthorizedError("Refresh token not found or revoked")

    if stored_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise UnauthorizedError("Refresh token expired")

    # Revoke old token
    stored_token.revoked = True

    # Fetch user
    user_result = await db.execute(
        select(User).where(User.id == stored_token.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    # Issue new tokens
    token_data = {"sub": str(user.id), "role": user.role}
    new_access = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(new_refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(new_rt)
    await db.flush()

    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


async def logout_user(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_token(refresh_token)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .values(revoked=True)
    )


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    return user


async def update_user_profile(
    db: AsyncSession, user_id: uuid.UUID, data: UserUpdateRequest
) -> UserResponse:
    user = await get_user_by_id(db, user_id)
    update_data = data.model_dump(exclude_unset=True)

    if "phone" in update_data and update_data["phone"]:
        phone_exists = await db.execute(
            select(User).where(User.phone == update_data["phone"], User.id != user_id)
        )
        if phone_exists.scalar_one_or_none():
            raise ConflictError("Phone number already in use")

        # If phone is changing, require OTP verification
        if update_data["phone"] != user.phone:
            try:
                import redis.asyncio as _redis
                r = _redis.from_url(settings.redis_url)
                verified = await r.get(f"phone_verified:{update_data['phone']}")
                await r.aclose()
                if not verified:
                    raise BadRequestError("New phone number must be verified via OTP.")
                r = _redis.from_url(settings.redis_url)
                await r.delete(f"phone_verified:{update_data['phone']}")
                await r.aclose()
            except (ConnectionError, OSError):
                pass  # Redis not available — skip check in dev

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)
