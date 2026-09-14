"""
Callback-data URL store.

Root cause this fixes
----------------------
Telegram limits inline button `callback_data` to 64 bytes. The bot used to
embed the full URL directly in callback_data (e.g. "dl:video:{url}"). Any
share link with tracking parameters (Instagram "?utm_source=...", YouTube
"?si=...", etc.) easily exceeds that limit, so Telegram rejects the button
with "BUTTON_DATA_INVALID" and the whole handler crashes — for EVERY
platform that goes through the Video/Audio choice buttons (i.e. everything
except Pinterest, which skips buttons entirely).

Fix: never put the URL in callback_data. Store the URL in Redis under a
short random token and put only the token in callback_data.
"""

import logging
from typing import Optional
from uuid import uuid4

from app.core.cache.file_id_cache import get_redis

logger = logging.getLogger(__name__)

_PREFIX = "cb:url:"
_TTL_SECONDS = 30 * 60  # 30 daqiqa — tugmani bosish uchun yetarli vaqt


async def store_url(url: str) -> str:
    """Save URL under a short token and return that token."""
    redis = await get_redis()
    token = uuid4().hex[:12]
    await redis.set(f"{_PREFIX}{token}", url, ex=_TTL_SECONDS)
    return token


async def resolve_token(token: str) -> Optional[str]:
    """Return the original URL for a token, or None if expired/unknown."""
    redis = await get_redis()
    return await redis.get(f"{_PREFIX}{token}")
