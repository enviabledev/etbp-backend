import re

import redis.asyncio as redis
from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.config import settings
from app.core.exceptions import BadRequestError
from app.integrations.termii import TermiiClient

router = APIRouter(prefix="/otp", tags=["OTP"])


class SendOTPRequest(BaseModel):
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = re.sub(r"[\s\-\(\)]", "", v)
        if not re.match(r"^\+[1-9]\d{6,14}$", v):
            raise ValueError(
                "Phone number must be in international format (e.g., +2348012345678)"
            )
        return v


class VerifyOTPRequest(BaseModel):
    phone_number: str
    pin: str

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str) -> str:
        if not re.match(r"^\d{6}$", v):
            raise ValueError("OTP must be 6 digits")
        return v


@router.post("/send")
async def send_otp(data: SendOTPRequest):
    """Send OTP to phone number for verification."""
    # Dev bypass — don't call Termii
    if not settings.termii_api_key or settings.app_env == "development":
        return {
            "message": "OTP sent (dev mode — use any 6 digits to verify)",
            "phone_number": data.phone_number,
        }

    try:
        client = TermiiClient()
        result = await client.send_otp(data.phone_number)
        pin_id = result.get("pinId")

        if not pin_id:
            raise BadRequestError("Failed to send OTP. Please try again.")

        # Store pin_id in Redis (10 min TTL)
        r = redis.from_url(settings.redis_url)
        await r.setex(f"otp:{data.phone_number}", 600, pin_id)
        await r.aclose()

        return {"message": "OTP sent successfully", "phone_number": data.phone_number}
    except BadRequestError:
        raise
    except Exception as e:
        raise BadRequestError(f"Failed to send OTP: {e}")


@router.post("/verify")
async def verify_otp(data: VerifyOTPRequest):
    """Verify OTP for a phone number."""
    # Dev bypass — accept any 6-digit pin
    if not settings.termii_api_key or settings.app_env == "development":
        r = redis.from_url(settings.redis_url)
        await r.setex(f"phone_verified:{data.phone_number}", 1800, "verified")
        await r.aclose()
        return {
            "message": "Phone number verified (dev mode)",
            "verified": True,
            "phone_number": data.phone_number,
        }

    # Get pin_id from Redis
    r = redis.from_url(settings.redis_url)
    pin_id = await r.get(f"otp:{data.phone_number}")
    await r.aclose()

    if not pin_id:
        raise BadRequestError("OTP expired or not found. Please request a new one.")

    pin_id_str = pin_id.decode() if isinstance(pin_id, bytes) else pin_id

    try:
        client = TermiiClient()
        result = await client.verify_otp(pin_id_str, data.pin)

        if result.get("verified") is True or result.get("status") == "success":
            r = redis.from_url(settings.redis_url)
            await r.setex(
                f"phone_verified:{data.phone_number}", 1800, "verified"
            )
            await r.delete(f"otp:{data.phone_number}")
            await r.aclose()

            return {
                "message": "Phone number verified",
                "verified": True,
                "phone_number": data.phone_number,
            }
        else:
            raise BadRequestError("Invalid OTP. Please try again.")
    except BadRequestError:
        raise
    except Exception as e:
        raise BadRequestError(f"OTP verification failed: {e}")
