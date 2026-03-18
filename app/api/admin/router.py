from fastapi import APIRouter

from app.api.admin import agents, bookings, drivers, maintenance, notifications, promos, reports, reviews, routes, schedules, settings, users, vehicles

router = APIRouter(prefix="/admin")

router.include_router(routes.router)
router.include_router(schedules.router)
router.include_router(vehicles.router)
router.include_router(drivers.router)
router.include_router(agents.router)
router.include_router(bookings.router)
router.include_router(users.router)
router.include_router(promos.router)
router.include_router(reports.router)
router.include_router(settings.router)
router.include_router(notifications.router)
router.include_router(reviews.router)
router.include_router(maintenance.router)
