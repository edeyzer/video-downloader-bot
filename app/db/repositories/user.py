"""
User repository — CRUD operations for User model.
"""

from datetime import date, datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        user_id: int,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> tuple[User, bool]:
        """
        Returns (user, created: bool)
        """
        user = await self.get_by_id(user_id)
        if user:
            # Update profile info if changed
            changed = False
            if username is not None and user.username != username:
                user.username = username
                changed = True
            if full_name is not None and user.full_name != full_name:
                user.full_name = full_name
                changed = True
            if language_code is not None and user.language_code != language_code:
                user.language_code = language_code
                changed = True
            if changed:
                user.updated_at = datetime.now(timezone.utc)
                await self.session.flush()
            return user, False

        user = User(
            id=user_id,
            username=username,
            full_name=full_name,
            language_code=language_code or "uz",
        )
        self.session.add(user)
        await self.session.flush()
        return user, True

    async def update_premium_status(
        self,
        user_id: int,
        is_premium: bool,
        premium_until: Optional[datetime] = None,
    ) -> Optional[User]:
        user = await self.get_by_id(user_id)
        if not user:
            return None
        user.is_premium = is_premium
        user.premium_until = premium_until
        user.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user

    async def increment_daily_download(self, user_id: int) -> Optional[User]:
        """
        Atomically increment daily_downloads.
        Resets counter if last_download_date is not today.
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        today = date.today()
        if user.last_download_date != today:
            user.daily_downloads = 1
            user.last_download_date = today
        else:
            user.daily_downloads += 1

        user.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user

    async def reset_daily_limits(self) -> int:
        """
        Reset daily_downloads for all users (can be run by cron / celery beat).
        Returns number of affected rows.
        """
        today = date.today()
        stmt = (
            update(User)
            .where(User.last_download_date < today)
            .values(daily_downloads=0, last_download_date=today)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def set_blocked(self, user_id: int, blocked: bool = True) -> Optional[User]:
        user = await self.get_by_id(user_id)
        if not user:
            return None
        user.is_blocked = blocked
        user.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user

    async def get_admins(self) -> Sequence[User]:
        result = await self.session.execute(select(User).where(User.is_admin.is_(True)))
        return result.scalars().all()

    async def count_users(self) -> int:
        result = await self.session.execute(select(func.count(User.id)))
        return result.scalar_one()

    async def count_premium_users(self) -> int:
        result = await self.session.execute(
            select(func.count(User.id)).where(User.is_premium.is_(True))
        )
        return result.scalar_one()