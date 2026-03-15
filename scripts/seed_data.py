"""Seed database with sample terminals, routes, vehicle types, and schedules."""
import asyncio
from datetime import date, time

from app.database import async_session_factory
from app.models.route import Route, RouteStop, Terminal
from app.models.vehicle import VehicleType


async def seed():
    async with async_session_factory() as db:
        # Terminals
        lagos = Terminal(name="Lagos Terminal (Jibowu)", code="LAG", city="Lagos", state="Lagos", address="Jibowu, Yaba, Lagos")
        ibadan = Terminal(name="Ibadan Terminal (Challenge)", code="IBD", city="Ibadan", state="Oyo", address="Challenge, Ibadan")
        abuja = Terminal(name="Abuja Terminal (Utako)", code="ABJ", city="Abuja", state="FCT", address="Utako, Abuja")
        benin = Terminal(name="Benin Terminal", code="BEN", city="Benin City", state="Edo", address="Ring Road, Benin City")
        ph = Terminal(name="Port Harcourt Terminal", code="PHC", city="Port Harcourt", state="Rivers", address="Rumuola, Port Harcourt")
        enugu = Terminal(name="Enugu Terminal", code="ENU", city="Enugu", state="Enugu", address="Ogbete, Enugu")

        for t in [lagos, ibadan, abuja, benin, ph, enugu]:
            db.add(t)
        await db.flush()

        # Vehicle types
        standard = VehicleType(name="Standard Bus", description="18-seater standard bus", seat_capacity=18, seat_layout={"rows": 5, "columns": 4, "skip": [3]})
        executive = VehicleType(name="Executive Bus", description="14-seater executive bus with AC", seat_capacity=14, seat_layout={"rows": 4, "columns": 4, "skip": [3]})
        sienna = VehicleType(name="Sienna", description="7-seater Toyota Sienna", seat_capacity=7, seat_layout={"rows": 3, "columns": 3, "skip": []})

        for vt in [standard, executive, sienna]:
            db.add(vt)
        await db.flush()

        # Routes
        lag_ibd = Route(name="Lagos → Ibadan", code="LAG-IBD", origin_terminal_id=lagos.id, destination_terminal_id=ibadan.id, distance_km=128, estimated_duration_minutes=150, base_price=5500)
        lag_abj = Route(name="Lagos → Abuja", code="LAG-ABJ", origin_terminal_id=lagos.id, destination_terminal_id=abuja.id, distance_km=750, estimated_duration_minutes=600, base_price=25000)
        lag_ben = Route(name="Lagos → Benin", code="LAG-BEN", origin_terminal_id=lagos.id, destination_terminal_id=benin.id, distance_km=312, estimated_duration_minutes=300, base_price=10000)
        lag_ph = Route(name="Lagos → Port Harcourt", code="LAG-PHC", origin_terminal_id=lagos.id, destination_terminal_id=ph.id, distance_km=600, estimated_duration_minutes=540, base_price=22000)
        abj_enu = Route(name="Abuja → Enugu", code="ABJ-ENU", origin_terminal_id=abuja.id, destination_terminal_id=enugu.id, distance_km=315, estimated_duration_minutes=300, base_price=12000)

        for r in [lag_ibd, lag_abj, lag_ben, lag_ph, abj_enu]:
            db.add(r)
        await db.flush()

        await db.commit()
        print("Seed data created successfully!")
        print(f"  Terminals: 6")
        print(f"  Vehicle types: 3")
        print(f"  Routes: 5")


if __name__ == "__main__":
    asyncio.run(seed())
