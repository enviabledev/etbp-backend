import httpx

from app.config import settings


class GoogleMapsClient:
    BASE_URL = "https://maps.googleapis.com/maps/api"

    def __init__(self):
        self.api_key = settings.google_maps_api_key

    async def get_distance_matrix(
        self, origin: str, destination: str
    ) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/distancematrix/json",
                params={
                    "origins": origin,
                    "destinations": destination,
                    "key": self.api_key,
                },
            )
            response.raise_for_status()
            data = response.json()

            if data.get("rows"):
                element = data["rows"][0]["elements"][0]
                return {
                    "distance_km": element["distance"]["value"] / 1000,
                    "duration_minutes": element["duration"]["value"] / 60,
                }
            return {}

    async def geocode(self, address: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/geocode/json",
                params={"address": address, "key": self.api_key},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("results"):
                location = data["results"][0]["geometry"]["location"]
                return {"latitude": location["lat"], "longitude": location["lng"]}
            return {}
