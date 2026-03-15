import httpx

from app.config import settings


class TermiiClient:
    BASE_URL = "https://api.ng.termii.com/api"

    def __init__(self):
        self.api_key = settings.termii_api_key
        self.sender_id = settings.termii_sender_id

    async def send_sms(self, to: str, message: str) -> dict:
        payload = {
            "api_key": self.api_key,
            "to": to,
            "from": self.sender_id,
            "sms": message,
            "type": "plain",
            "channel": "generic",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/sms/send", json=payload
            )
            response.raise_for_status()
            return response.json()

    async def send_otp(self, to: str, otp: str) -> dict:
        message = f"Your ETBP verification code is {otp}. Valid for 10 minutes."
        return await self.send_sms(to, message)
