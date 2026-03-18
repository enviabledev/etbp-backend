from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserResponse, UserUpdateRequest
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest, db: DBSession):
    return await auth_service.register_user(db, data)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: DBSession):
    return await auth_service.login_user(db, data)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: DBSession):
    return await auth_service.refresh_tokens(db, data.refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(data: LogoutRequest, db: DBSession):
    await auth_service.logout_user(db, data.refresh_token)
    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser):
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(data: UserUpdateRequest, db: DBSession, current_user: CurrentUser):
    return await auth_service.update_user_profile(db, current_user.id, data)


class GoogleAuthRequest(BaseModel):
    id_token: str


class AppleAuthRequest(BaseModel):
    id_token: str
    first_name: str | None = None
    last_name: str | None = None


class LinkSocialRequest(BaseModel):
    id_token: str


@router.post("/google")
async def google_auth(data: GoogleAuthRequest, db: DBSession):
    from app.services.social_auth_service import google_sign_in
    return await google_sign_in(db, data.id_token)


@router.post("/apple")
async def apple_auth(data: AppleAuthRequest, db: DBSession):
    from app.services.social_auth_service import apple_sign_in
    return await apple_sign_in(db, data.id_token, data.first_name, data.last_name)


@router.post("/biometric-check")
async def biometric_check(current_user: CurrentUser):
    return {"valid": True, "user_id": str(current_user.id), "email": current_user.email}


@router.post("/link-google")
async def link_google(data: LinkSocialRequest, db: DBSession, current_user: CurrentUser):
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    try:
        idinfo = google_id_token.verify_oauth2_token(
            data.id_token, google_requests.Request(), None
        )
        google_user_id = idinfo["sub"]
    except Exception:
        from app.core.exceptions import UnauthorizedError
        raise UnauthorizedError("Invalid Google token")
    from app.services.social_auth_service import link_social_account
    return await link_social_account(db, current_user.id, "google", google_user_id)


@router.post("/link-apple")
async def link_apple(data: LinkSocialRequest, db: DBSession, current_user: CurrentUser):
    import jwt as pyjwt
    import httpx
    try:
        header = pyjwt.get_unverified_header(data.id_token)
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://appleid.apple.com/auth/keys")
            apple_keys = resp.json()["keys"]
        key_data = next((k for k in apple_keys if k["kid"] == header.get("kid")), None)
        from jwt.algorithms import RSAAlgorithm
        public_key = RSAAlgorithm.from_jwk(key_data)
        payload = pyjwt.decode(data.id_token, public_key, algorithms=["RS256"],
                               audience=settings.apple_client_id, issuer="https://appleid.apple.com")
        apple_user_id = payload["sub"]
    except Exception:
        from app.core.exceptions import UnauthorizedError
        raise UnauthorizedError("Invalid Apple token")
    from app.services.social_auth_service import link_social_account
    return await link_social_account(db, current_user.id, "apple", apple_user_id)
