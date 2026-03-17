import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin.router import router as admin_router
from app.api.agent.router import router as agent_router
from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.exceptions import AppException
from app.core.middleware import RequestLoggingMiddleware

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def _booking_expiry_loop():
    """Background loop that expires stale pending bookings and releases expired seat locks."""
    from app.tasks.booking_expiry import expire_pending_bookings, release_expired_seat_locks

    while True:
        try:
            await expire_pending_bookings()
            await release_expired_seat_locks()
        except Exception as e:
            logging.getLogger(__name__).error("Booking expiry loop error: %s", e)
        await asyncio.sleep(300)  # Run every 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch background task
    task = asyncio.create_task(_booking_expiry_loop())
    logging.getLogger(__name__).info("Started booking expiry background task")
    yield
    # Shutdown: cancel background task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Enviable Transport Booking Platform API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging
    app.add_middleware(RequestLoggingMiddleware)

    # Exception handlers
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logging.getLogger(__name__).exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # Routers
    app.include_router(v1_router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=settings.api_prefix)
    app.include_router(agent_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "app": settings.app_name}

    return app


app = create_app()
