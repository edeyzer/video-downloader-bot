"""
DownloadLog repository — analytics, limits and cache-related operations.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence

from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DownloadLog


class DownloadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        url: str,
        platform: Optional[str] = None,
        media_type: Optional[str] = None,
        quality: Optional[str] = None,
        status: str = "pending",
    ) -> DownloadLog:
        log = DownloadLog(
            user_id=user_id,
            url=url,
            platform=platform,
            media_type=media_type,
            quality=quality,
            status=status,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_by_id(self, log_id: int) -> Optional[DownloadLog]:
        result = await self.session.execute(
            select(DownloadLog).where(DownloadLog.id == log_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        log_id: int,
        status: str,
        file_id: Optional[str] = None,
        file_unique_id: Optional[str] = None,
        file_size: Optional[int] = None,
        error_message: Optional[str] = None,
        download_time_ms: Optional[int] = None,
        was_cached: bool = False,
    ) -> Optional[DownloadLog]:
        log = await self.get_by_id(log_id)
        if not log:
            return None

        log.status = status
        if file_id is not None:
            log.file_id = file_id
        if file_unique_id is not None:
            log.file_unique_id = file_unique_id
        if file_size is not None:
            log.file_size = file_size
        if error_message is not None:
            log.error_message = error_message
        if download_time_ms is not None:
            log.download_time_ms = download_time_ms
        log.was_cached = was_cached

        await self.session.flush()
        return log

    async def find_cached_by_url(self, url: str) -> Optional[DownloadLog]:
        """
        Find a successful download with file_id for the same URL.
        Used for smart caching (Redis is primary, this is fallback / analytics).
        """
        stmt = (
            select(DownloadLog)
            .where(
                and_(
                    DownloadLog.url == url,
                    DownloadLog.status == "success",
                    DownloadLog.file_id.is_not(None),
                )
            )
            .order_by(desc(DownloadLog.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_downloads(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[DownloadLog]:
        stmt = (
            select(DownloadLog)
            .where(DownloadLog.user_id == user_id)
            .order_by(desc(DownloadLog.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_user_downloads_today(self, user_id: int) -> int:
        """Count successful downloads by user since midnight UTC."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = (
            select(func.count(DownloadLog.id))
            .where(
                and_(
                    DownloadLog.user_id == user_id,
                    DownloadLog.status.in_(["success", "cached"]),
                    DownloadLog.created_at >= today_start,
                )
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_stats(
        self,
        days: int = 7,
    ) -> dict:
        """Simple aggregate stats for admin panel."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        total_stmt = select(func.count(DownloadLog.id)).where(
            DownloadLog.created_at >= since
        )
        success_stmt = select(func.count(DownloadLog.id)).where(
            and_(
                DownloadLog.created_at >= since,
                DownloadLog.status.in_(["success", "cached"]),
            )
        )
        cached_stmt = select(func.count(DownloadLog.id)).where(
            and_(
                DownloadLog.created_at >= since,
                DownloadLog.was_cached.is_(True),
            )
        )
        failed_stmt = select(func.count(DownloadLog.id)).where(
            and_(
                DownloadLog.created_at >= since,
                DownloadLog.status == "failed",
            )
        )

        total = (await self.session.execute(total_stmt)).scalar_one()
        success = (await self.session.execute(success_stmt)).scalar_one()
        cached = (await self.session.execute(cached_stmt)).scalar_one()
        failed = (await self.session.execute(failed_stmt)).scalar_one()

        return {
            "period_days": days,
            "total": total,
            "success": success,
            "cached": cached,
            "failed": failed,
            "cache_hit_rate": round(cached / success * 100, 2) if success else 0.0,
        }