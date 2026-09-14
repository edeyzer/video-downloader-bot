"""
Database session middleware.
Injects AsyncSession and repositories into handler data.
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.db.repositories import UserRepository, SubscriptionRepository, DownloadRepository


class DbSessionMiddleware(BaseMiddleware):
    """
    Opens a new AsyncSession for every update and
    injects it + repositories into handler kwargs.

    Usage in handler:
        async def handler(message: Message, session: AsyncSession, user_repo: UserRepository):
            ...
    """

    def __init__(self):
        self.session_factory = get_session_factory()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            # Inject session
            data["session"] = session

            # Inject repositories
            data["user_repo"] = UserRepository(session)
            data["sub_repo"] = SubscriptionRepository(session)
            data["download_repo"] = DownloadRepository(session)

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise