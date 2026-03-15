from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "etbp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Lagos",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.conf.beat_schedule = {
    "expire-pending-bookings": {
        "task": "expire_pending_bookings",
        "schedule": 300.0,  # Every 5 minutes
    },
    "release-expired-seat-locks": {
        "task": "release_expired_seat_locks",
        "schedule": 60.0,  # Every minute
    },
    "send-trip-reminders": {
        "task": "send_trip_reminders",
        "schedule": crontab(hour=18, minute=0),  # 6 PM daily
    },
}

celery_app.autodiscover_tasks(["app.tasks"])
