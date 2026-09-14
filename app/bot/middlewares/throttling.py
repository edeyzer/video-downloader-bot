"""
Rate-limiting / Anti-spam middleware powered by Redis.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from app.config import get_settings
from app.core.cache.file_id_cache import get_redis

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """
    Simple sliding-window rate limiter.

    Key:  throttle:{user_id}
    Value: number of requests in current window

    If limit exceeded → answer with warning and stop propagation.
    """

    def __init__(
        self,
        limit: Optional[int] = None,
        window: Optional[int] = None,
    ):
        settings = get_settings()
        self.limit = limit or settings.rate_limit_requests
        self.window = window or settings.rate_limit_window  # seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message) and event.from_user:
            user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user

        if user is None:
            return await handler(event, data)

        # Admins are never throttled
        settings = get_settings()
        if user.id in settings.admin_ids:
            return await handler(event, data)

        redis = await get_redis()
        key = f"throttle:{user.id}"

        # Increment and set TTL if first request
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, self.window)

        if current > self.limit:
            ttl = await redis.ttl(key)
            warning = (
                f"⚠️ Juda ko‘p so‘rov yubordingiz.\n"
                f"Iltimos, <b>{ttl}</b> soniyadan keyin qayta urinib ko‘ring."
            )
            try:
                if isinstance(event, Message):
                    await event.answer(warning)
                elif isinstance(event, CallbackQuery):
                    await event.answer(warning, show_alert=True)
            except Exception:
                pass
            logger.info("User %s throttled (%s/%s)", user.id, current, self.limit)
            return None  # stop propagation

        return await handler(event, data)