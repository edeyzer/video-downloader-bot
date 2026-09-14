"""
Celery application configuration.
Broker & backend = Redis.
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "video_downloader",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks.download"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=60 * 15,          # 15 min hard limit
    task_soft_time_limit=60 * 12,     # 12 min soft limit
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=60 * 60 * 24,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.task_routes = {
    "app.workers.tasks.download.*": {"queue": "downloads"},
}