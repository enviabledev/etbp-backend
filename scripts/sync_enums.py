"""Sync PostgreSQL enum types with the application's canonical values.
Run: python scripts/sync_enums.py
"""
import asyncio
from sqlalchemy import text
from app.database import engine

TRIP_STATUSES = ["scheduled", "boarding", "departed", "en_route", "arrived", "completed", "cancelled", "delayed"]
BOOKING_STATUSES = ["pending", "confirmed", "checked_in", "completed", "cancelled", "expired", "no_show"]


async def sync():
    async with engine.begin() as conn:
        for status in TRIP_STATUSES:
            try:
                await conn.execute(text(f"ALTER TYPE trip_status ADD VALUE IF NOT EXISTS '{status}'"))
            except Exception as e:
                print(f"  trip_status '{status}': {e}")
        for status in BOOKING_STATUSES:
            try:
                await conn.execute(text(f"ALTER TYPE booking_status ADD VALUE IF NOT EXISTS '{status}'"))
            except Exception as e:
                print(f"  booking_status '{status}': {e}")
    print("Enum sync complete")


if __name__ == "__main__":
    asyncio.run(sync())
