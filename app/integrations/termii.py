import httpx

from app.config import settings


class TermiiClient:
    BASE_URL = "https://v3.api.termii.com"

    def __init__(self):
        self.api_key = settings.termii_api_key
        self.sender_id = settings.termii_sender_id

    async def send_sms(self, to: str, message: str) -> dict:
        """Send a plain SMS message."""
        payload = {
            "api_key": self.api_key,
            "to": to.lstrip("+"),
            "from": self.sender_id,
            "sms": message,
            "type": "plain",
            "channel": "generic",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/api/sms/send", json=payload
            )
            response.raise_for_status()
            return response.json()

    async def send_otp(self, phone_number: str) -> dict:
        """Send OTP to a phone number via Termii's OTP API.
        Phone must be in international format (e.g., +2348012345678)."""
        phone = phone_number.lstrip("+")

        payload = {
            "api_key": self.api_key,
            "message_type": "NUMERIC",
            "to": phone,
            "from": self.sender_id or "N-Alert",
            "channel": "dnd",
            "pin_attempts": 3,
            "pin_time_limit": 10,
            "pin_length": 6,
            "pin_placeholder": "< 1234 >",
            "message_text": "Your Enviable Transport verification code is < 1234 >. Valid for 10 minutes.",
            "pin_type": "NUMERIC",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/api/sms/otp/send",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def verify_otp(self, pin_id: str, pin: str) -> dict:
        """Verify an OTP pin."""
        payload = {
            "api_key": self.api_key,
            "pin_id": pin_id,
            "pin": pin,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/api/sms/otp/verify",
                json=payload,
            )
            response.raise_for_status()
            return response.json()
