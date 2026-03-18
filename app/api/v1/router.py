from fastapi import APIRouter

from app.api.v1 import agent, auth, banners, bookings, driver, lost_found as lost_found_routes, messaging, notifications, otp, payments, promo, reviews, routes, schedules, seats, support, terminals, users

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(otp.router)
router.include_router(users.router)
router.include_router(terminals.router)
router.include_router(routes.router)
router.include_router(schedules.router)
router.include_router(seats.router)
router.include_router(bookings.router)
router.include_router(payments.router)
router.include_router(reviews.router)
router.include_router(support.router)
router.include_router(driver.router)
router.include_router(notifications.router)
router.include_router(agent.router)
router.include_router(promo.router)
router.include_router(banners.router)
router.include_router(messaging.router)
router.include_router(lost_found_routes.router)
