"""
Subscription repository — CRUD for premium subscriptions.
"""

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, User


class SubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        plan: str,
        amount: Optional[int] = None,
        currency: str = "UZS",
        expires_at: Optional[datetime] = None,
        payment_provider: Optional[str] = None,
        payment_id: Optional[str] = None,
        is_active: bool = True,
    ) -> Subscription:
        sub = Subscription(
            user_id=user_id,
            plan=plan,
            amount=amount,
            currency=currency,
            expires_at=expires_at,
            payment_provider=payment_provider,
            payment_id=payment_id,
            is_active=is_active,
        )
        self.session.add(sub)
        await self.session.flush()
        return sub

    async def get_active_by_user(self, user_id: int) -> Optional[Subscription]:
        """Return the currently active subscription for a user (if any)."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(Subscription)
            .where(
                and_(
                    Subscription.user_id == user_id,
                    Subscription.is_active.is_(True),
                    (Subscription.expires_at.is_(None)) | (Subscription.expires_at > now),
                )
            )
            .order_by(Subscription.started_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, sub_id: int) -> Optional[Subscription]:
        result = await self.session.execute(
            select(Subscription).where(Subscription.id == sub_id)
        )
        return result.scalar_one_or_none()

    async def deactivate(self, sub_id: int) -> Optional[Subscription]:
        sub = await self.get_by_id(sub_id)
        if not sub:
            return None
        sub.is_active = False
        await self.session.flush()
        return sub

    async def deactivate_all_for_user(self, user_id: int) -> int:
        """Deactivate all active subscriptions for a user."""
        stmt = (
            update(Subscription)
            .where(
                and_(
                    Subscription.user_id == user_id,
                    Subscription.is_active.is_(True),
                )
            )
            .values(is_active=False)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def get_history(self, user_id: int, limit: int = 20) -> Sequence[Subscription]:
        stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.started_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def expire_outdated(self) -> int:
        """
        Mark expired subscriptions as inactive.
        Can be run periodically by Celery Beat.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(Subscription)
            .where(
                and_(
                    Subscription.is_active.is_(True),
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at <= now,
                )
            )
            .values(is_active=False)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount