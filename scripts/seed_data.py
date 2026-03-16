"""Seed database with realistic sample data for ETBP."""
import asyncio
import random
import string
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core.constants import BookingStatus, PaymentStatus, UserRole
from app.core.security import generate_booking_reference, hash_password
from app.database import async_session_factory
from app.models.booking import Booking, BookingPassenger
from app.models.driver import Driver
from app.models.payment import Payment
from app.models.route import Route, Terminal
from app.models.schedule import Schedule, Trip, TripSeat
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleType

# Van layouts: 3 seats per row (2 left + aisle + 1 right)
STANDARD_VAN_LAYOUT = {
    "columns": 3,
    "arrangement": "2-aisle-1",
    "rows": [
        {"row": 1, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 2, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 3, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 4, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "middle"}, {"col": 3, "type": "window"}]},
        {"row": 5, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "middle"}]},  # back row: 2 extra
    ],
}

EXECUTIVE_VAN_LAYOUT = {
    "columns": 3,
    "arrangement": "2-aisle-1",
    "rows": [
        {"row": 1, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 2, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 3, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
        {"row": 4, "seats": [{"col": 1, "type": "window"}, {"col": 2, "type": "aisle"}, {"col": 3, "type": "window"}]},
    ],
}


def _gen_seats(trip_id, capacity: int, columns: int = 3) -> list[TripSeat]:
    """Generate seats with simple integer numbering: 1, 2, 3..."""
    seats = []
    num = 0
    row = 1
    while num < capacity:
        for col in range(1, columns + 1):
            num += 1
            if num > capacity:
                break
            seat_type = "window" if col in (1, columns) else "aisle"
            seats.append(TripSeat(
                trip_id=trip_id,
                seat_number=str(num),
                seat_row=row,
                seat_column=col,
                seat_type=seat_type,
            ))
        row += 1
    return seats


async def seed():
    async with async_session_factory() as db:
        existing = await db.execute(select(User).where(User.email == "admin@enviabletransport.com"))
        if existing.scalar_one_or_none():
            print("Database already seeded. Skipping.")
            return

        print("Seeding database...")

        # ── Terminals ──
        terminals_data = [
            ("Lagos Terminal (Jibowu)", "LAG-JBW", "Lagos", "Lagos", "15 Ikorodu Rd, Jibowu, Yaba, Lagos", 6.5158, 3.3787, "+2341234001"),
            ("Lagos Terminal (Berger)", "LAG-BRG", "Lagos", "Lagos", "Berger Bus Stop, Ojodu, Lagos", 6.6317, 3.3507, "+2341234002"),
            ("Abuja Terminal (Utako)", "ABJ", "Abuja", "FCT", "Plot 123, Utako District, Abuja", 9.0579, 7.4951, "+2341234003"),
            ("Benin Terminal (Ring Road)", "BEN", "Benin City", "Edo", "Ring Road, Benin City", 6.3350, 5.6037, "+2341234004"),
            ("Port Harcourt Terminal (Rumuola)", "PHC", "Port Harcourt", "Rivers", "Rumuola, Port Harcourt", 4.8156, 7.0498, "+2341234005"),
        ]
        terminals = {}
        for name, code, city, state, address, lat, lng, phone in terminals_data:
            t = Terminal(
                name=name, code=code, city=city, state=state,
                address=address, latitude=lat, longitude=lng, phone=phone,
                amenities={"wifi": True, "restroom": True, "snack_bar": True, "parking": True},
                opening_time=time(5, 0), closing_time=time(22, 0),
            )
            db.add(t)
            terminals[code] = t
        await db.flush()
        print(f"  Terminals: {len(terminals)}")

        # ── Vehicle Types (Vans) ──
        standard_van = VehicleType(
            name="Standard Van",
            description="14-seater air-conditioned van",
            seat_capacity=14,
            seat_layout=STANDARD_VAN_LAYOUT,
            amenities={"ac": True, "usb_charging": True},
        )
        executive_van = VehicleType(
            name="Executive Van",
            description="12-seater luxury van with extra legroom and WiFi",
            seat_capacity=12,
            seat_layout=EXECUTIVE_VAN_LAYOUT,
            amenities={"ac": True, "wifi": True, "usb_charging": True, "extra_legroom": True, "refreshments": True},
        )
        db.add_all([standard_van, executive_van])
        await db.flush()
        print(f"  Vehicle types: 2 (Standard Van 14-seat, Executive Van 12-seat)")

        # ── Vehicles ──
        vehicles = []
        plate_prefixes = ["LAG", "ABJ", "BEN"]
        for i, vt in enumerate([standard_van, standard_van, standard_van, executive_van, executive_van, executive_van]):
            v = Vehicle(
                vehicle_type_id=vt.id,
                plate_number=f"{plate_prefixes[i % 3]}-{random.randint(100,999)}-{random.choice(string.ascii_uppercase)}{random.choice(string.ascii_uppercase)}",
                make="Toyota",
                model="HiAce" if vt == standard_van else "Coaster",
                year=random.choice([2022, 2023, 2024]),
                color=random.choice(["White", "Blue", "Silver"]),
                current_mileage=random.randint(10000, 80000),
                insurance_expiry=date.today() + timedelta(days=random.randint(60, 365)),
            )
            db.add(v)
            vehicles.append(v)
        await db.flush()
        print(f"  Vehicles: {len(vehicles)}")

        # ── Routes ──
        routes_data = [
            ("Lagos (Jibowu) → Abuja", "JBW-ABJ", "LAG-JBW", "ABJ", 750, 540, 25000),
            ("Lagos (Jibowu) → Benin", "JBW-BEN", "LAG-JBW", "BEN", 312, 270, 10000),
            ("Lagos (Berger) → Port Harcourt", "BRG-PHC", "LAG-BRG", "PHC", 600, 480, 22000),
            ("Abuja → Benin", "ABJ-BEN", "ABJ", "BEN", 420, 360, 15000),
        ]
        routes = {}
        for name, code, origin_code, dest_code, km, mins, price in routes_data:
            r = Route(
                name=name, code=code,
                origin_terminal_id=terminals[origin_code].id,
                destination_terminal_id=terminals[dest_code].id,
                distance_km=km, estimated_duration_minutes=mins, base_price=price,
                luggage_policy="1 main luggage (max 23kg) + 1 carry-on (max 7kg). Extra luggage NGN 2,000 per item.",
            )
            db.add(r)
            routes[code] = r
        await db.flush()
        print(f"  Routes: {len(routes)}")

        # ── Schedules ──
        schedules = []
        for route_code, route_obj in routes.items():
            for i, dep_time in enumerate([time(6, 0), time(7, 30)]):
                vtype = standard_van if i == 0 else executive_van
                s = Schedule(
                    route_id=route_obj.id,
                    vehicle_type_id=vtype.id,
                    departure_time=dep_time,
                    recurrence="daily",
                    valid_from=date.today(),
                    valid_until=date.today() + timedelta(days=90),
                    price_override=float(route_obj.base_price) * (1.3 if vtype == executive_van else 1.0),
                )
                db.add(s)
                schedules.append(s)
        await db.flush()
        print(f"  Schedules: {len(schedules)}")

        # ── Trips (next 14 days) ──
        trip_count = 0
        all_trips = []
        for schedule in schedules:
            vtype_result = await db.execute(
                select(VehicleType).where(VehicleType.id == schedule.vehicle_type_id)
            )
            vtype = vtype_result.scalar_one()
            price = float(schedule.price_override) if schedule.price_override else float(routes[list(routes.keys())[0]].base_price)

            for day_offset in range(14):
                dep_date = date.today() + timedelta(days=day_offset)
                trip = Trip(
                    schedule_id=schedule.id,
                    route_id=schedule.route_id,
                    departure_date=dep_date,
                    departure_time=schedule.departure_time,
                    price=price,
                    total_seats=vtype.seat_capacity,
                    available_seats=vtype.seat_capacity,
                )
                db.add(trip)
                await db.flush()

                seats = _gen_seats(trip.id, vtype.seat_capacity)
                for seat in seats:
                    db.add(seat)

                all_trips.append(trip)
                trip_count += 1

        await db.flush()
        print(f"  Trips: {trip_count} (14 days × {len(schedules)} schedules)")

        # ── Users ──
        admin = User(
            email="admin@enviabletransport.com",
            password_hash=hash_password("Admin123!"),
            first_name="Super", last_name="Admin",
            role=UserRole.SUPER_ADMIN,
            email_verified=True, phone_verified=True, is_active=True,
            phone="+2348000000001",
        )
        # Agents — assigned to terminals
        agent1 = User(
            email="agent.jibowu@enviabletransport.com",
            password_hash=hash_password("Agent123!"),
            first_name="Blessing", last_name="Okonkwo",
            role=UserRole.AGENT, email_verified=True, is_active=True,
            phone="+2348000000002",
        )
        agent2 = User(
            email="agent.abuja@enviabletransport.com",
            password_hash=hash_password("Agent123!"),
            first_name="Chidi", last_name="Eze",
            role=UserRole.AGENT, email_verified=True, is_active=True,
            phone="+2348000000003",
        )
        db.add_all([admin, agent1, agent2])
        await db.flush()

        # Drivers — with profiles and terminal assignments
        driver_data = [
            ("Chukwu", "Obi", "chukwu@enviabletransport.com", "+2348200000001", "DRV-001-LAG", "C", "LAG-JBW"),
            ("Emeka", "Nwosu", "emeka.driver@enviabletransport.com", "+2348200000002", "DRV-002-ABJ", "C", "ABJ"),
            ("Funmi", "Adeyemi", "funmi@enviabletransport.com", "+2348200000003", "DRV-003-BEN", "B", "BEN"),
        ]
        drivers = []
        for fname, lname, email, phone, license_no, license_class, terminal_code in driver_data:
            u = User(
                email=email, password_hash=hash_password("Driver123!"),
                first_name=fname, last_name=lname,
                role=UserRole.DRIVER, phone=phone, is_active=True,
            )
            db.add(u)
            await db.flush()

            d = Driver(
                user_id=u.id,
                license_number=license_no,
                license_expiry=date.today() + timedelta(days=random.randint(180, 730)),
                license_class=license_class,
                years_experience=random.randint(3, 15),
                medical_check_expiry=date.today() + timedelta(days=random.randint(30, 365)),
                rating_avg=round(random.uniform(3.5, 5.0), 1),
                total_trips=random.randint(50, 500),
                is_available=True,
                assigned_terminal_id=terminals[terminal_code].id,
            )
            db.add(d)
            drivers.append(d)
        await db.flush()

        # Passenger users
        passengers = []
        passenger_data = [
            ("Adaeze", "Nwosu", "adaeze@gmail.com", "+2348101000001"),
            ("Emeka", "Okafor", "emeka@gmail.com", "+2348101000002"),
            ("Fatima", "Abdullahi", "fatima@gmail.com", "+2348101000003"),
            ("Kola", "Adeyemi", "kola@gmail.com", "+2348101000004"),
            ("Ngozi", "Igwe", "ngozi@gmail.com", "+2348101000005"),
        ]
        for fname, lname, email, phone in passenger_data:
            u = User(
                email=email, password_hash=hash_password("Pass123!"),
                first_name=fname, last_name=lname,
                role=UserRole.PASSENGER, phone=phone, is_active=True,
            )
            db.add(u)
            passengers.append(u)
        await db.flush()
        print(f"  Users: 1 admin, 2 agents, {len(drivers)} drivers, {len(passengers)} passengers")

        # ── Sample Bookings ──
        booking_count = 0
        for i in range(10):
            future_trips = [t for t in all_trips if t.departure_date >= date.today() and t.available_seats > 0]
            if not future_trips:
                break
            trip = random.choice(future_trips)
            passenger = random.choice(passengers)
            agent = random.choice([agent1, agent2, None])

            seats_result = await db.execute(
                select(TripSeat).where(
                    TripSeat.trip_id == trip.id,
                    TripSeat.status == "available",
                ).limit(random.randint(1, 2))
            )
            available_seats = seats_result.scalars().all()
            if not available_seats:
                continue

            ref = generate_booking_reference()
            total = float(trip.price) * len(available_seats)
            status = random.choice([BookingStatus.CONFIRMED, BookingStatus.CONFIRMED, BookingStatus.PENDING])

            booking = Booking(
                reference=ref, user_id=passenger.id, trip_id=trip.id,
                booked_by_user_id=agent.id if agent else None,
                total_amount=total, passenger_count=len(available_seats),
                contact_email=passenger.email, contact_phone=passenger.phone,
                status=status,
            )
            db.add(booking)
            await db.flush()

            for j, seat in enumerate(available_seats):
                bp = BookingPassenger(
                    booking_id=booking.id, seat_id=seat.id,
                    first_name=passenger.first_name if j == 0 else f"Guest{j}",
                    last_name=passenger.last_name, is_primary=(j == 0),
                    qr_code_data=f"{ref}-{seat.seat_number}-{passenger.first_name.upper()}",
                )
                db.add(bp)
                seat.status = "booked"

            trip.available_seats -= len(available_seats)

            if status == BookingStatus.CONFIRMED:
                payment = Payment(
                    booking_id=booking.id, user_id=passenger.id,
                    amount=total, method=random.choice(["card", "cash", "bank_transfer"]),
                    status=PaymentStatus.SUCCESSFUL,
                    gateway="paystack" if random.random() > 0.3 else "agent_portal",
                    paid_at=datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 48)),
                )
                db.add(payment)

            booking_count += 1

        await db.flush()
        await db.commit()

        print(f"  Bookings: {booking_count} with payments")
        print()
        print("Seed complete!")
        print()
        print("Login credentials:")
        print(f"  Admin:   admin@enviabletransport.com / Admin123!")
        print(f"  Agent1:  agent.jibowu@enviabletransport.com / Agent123!")
        print(f"  Agent2:  agent.abuja@enviabletransport.com / Agent123!")
        print(f"  Driver1: chukwu@enviabletransport.com / Driver123!")
        print(f"  Driver2: emeka.driver@enviabletransport.com / Driver123!")
        print(f"  Driver3: funmi@enviabletransport.com / Driver123!")
        print(f"  Passengers: adaeze@gmail.com (etc.) / Pass123!")


if __name__ == "__main__":
    asyncio.run(seed())
