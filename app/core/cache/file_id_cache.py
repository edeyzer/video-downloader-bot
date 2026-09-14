"""
Smart file_id cache service powered by Redis.
Stores Telegram file_id so repeated downloads return instantly.
"""

import hashlib
import json
import logging
from typing import Any, Optional

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class FileIdCache:
    """
    Redis-based cache for Telegram file_id.

    Key format:  cache:file_id:{sha256(url)[:24]}
    Value:       JSON  {"file_id": "...", "file_unique_id": "...", "media_type": "video", ...}
    TTL:         30 days (can be configured)
    """

    PREFIX = "cache:file_id:"
    DEFAULT_TTL = 60 * 60 * 24 * 30  # 30 days

    def __init__(self, redis: Redis, ttl: int = DEFAULT_TTL):
        self.redis = redis
        self.ttl = ttl

    @staticmethod
    def _make_key(url: str) -> str:
        """Create short deterministic key from URL."""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return f"{FileIdCache.PREFIX}{digest}"

    async def get(self, url: str) -> Optional[dict[str, Any]]:
        """
        Return cached data for URL or None.
        Example return:
            {
                "file_id": "BAACAgIAAxkBAAI...",
                "file_unique_id": "AgADqw...",
                "media_type": "video",
                "quality": "720p",
                "platform": "youtube"
            }
        """
        key = self._make_key(url)
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            logger.debug("Cache HIT for url=%s", url[:80])
            return data
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid cache data for key=%s, deleting", key)
            await self.redis.delete(key)
            return None

    async def set(
        self,
        url: str,
        file_id: str,
        file_unique_id: Optional[str] = None,
        media_type: Optional[str] = None,
        quality: Optional[str] = None,
        platform: Optional[str] = None,
        extra: Optional[dict] = None,
        ttl: Optional[int] = None,
    ) -> None:
        """Store file_id and metadata in Redis."""
        key = self._make_key(url)
        payload = {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "media_type": media_type,
            "quality": quality,
            "platform": platform,
        }
        if extra:
            payload.update(extra)

        await self.redis.set(
            key,
            json.dumps(payload, ensure_ascii=False),
            ex=ttl or self.ttl,
        )
        logger.debug("Cache SET for url=%s file_id=%s", url[:80], file_id[:30])

    async def delete(self, url: str) -> None:
        """Remove cache entry (e.g. when file becomes invalid)."""
        key = self._make_key(url)
        await self.redis.delete(key)

    async def exists(self, url: str) -> bool:
        key = self._make_key(url)
        return bool(await self.redis.exists(key))


# Singleton helper
_redis_client: Optional[Redis] = None
_cache_instance: Optional[FileIdCache] = None


async def get_redis() -> Redis:
    """Shared Redis connection for the whole app."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _redis_client


async def get_file_id_cache() -> FileIdCache:
    """Return singleton FileIdCache instance."""
    global _cache_instance
    if _cache_instance is None:
        redis = await get_redis()
        _cache_instance = FileIdCache(redis)
    return _cache_instance


async def close_redis() -> None:
    """Close Redis connection on shutdown."""
    global _redis_client, _cache_instance
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        _cache_instance = None