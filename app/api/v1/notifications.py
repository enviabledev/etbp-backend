from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from app.dependencies import CurrentUser, DBSession
from app.models.device_token import DeviceToken

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class RegisterDeviceRequest(BaseModel):
    token: str = Field(..., max_length=500)
    device_type: str = Field("android", max_length=20)
    app_type: str = Field("customer", max_length=20)


@router.post("/register-device")
async def register_device(data: RegisterDeviceRequest, db: DBSession, current_user: CurrentUser):
    # Check if token already exists for this user
    result = await db.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == current_user.id,
            DeviceToken.token == data.token,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.device_type = data.device_type
        existing.app_type = data.app_type
        existing.is_active = True
    else:
        # Deactivate old tokens for same user+app_type+device_type
        old_q = await db.execute(
            select(DeviceToken).where(
                DeviceToken.user_id == current_user.id,
                DeviceToken.app_type == data.app_type,
                DeviceToken.device_type == data.device_type,
            )
        )
        for old in old_q.scalars().all():
            old.is_active = False

        device_token = DeviceToken(
            user_id=current_user.id,
            token=data.token,
            device_type=data.device_type,
            app_type=data.app_type,
        )
        db.add(device_token)

    await db.flush()
    return {"registered": True}


@router.delete("/unregister-device")
async def unregister_device(data: RegisterDeviceRequest, db: DBSession, current_user: CurrentUser):
    await db.execute(
        delete(DeviceToken).where(
            DeviceToken.user_id == current_user.id,
            DeviceToken.token == data.token,
        )
    )
    await db.flush()
    return {"unregistered": True}
