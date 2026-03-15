import httpx

from app.config import settings


class PaystackClient:
    BASE_URL = "https://api.paystack.co"

    def __init__(self):
        self.secret_key = settings.paystack_secret_key
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_transaction(
        self,
        email: str,
        amount: int,
        reference: str,
        callback_url: str | None = None,
    ) -> dict:
        payload: dict = {
            "email": email,
            "amount": amount,
            "reference": reference,
            "currency": "NGN",
        }
        if callback_url:
            payload["callback_url"] = callback_url

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/transaction/initialize",
                json=payload,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json().get("data", {})

    async def verify_transaction(self, reference: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transaction/verify/{reference}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json().get("data", {})

    async def create_refund(self, transaction: str, amount: int | None = None) -> dict:
        payload: dict = {"transaction": transaction}
        if amount:
            payload["amount"] = amount

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/refund",
                json=payload,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json().get("data", {})
