"""
Async SQLAlchemy engine and session factory.
"""

from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings

_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Create (or return existing) async engine.

    NullPool ishlatiladi: Celery worker har bir vazifa uchun
    `asyncio.run(...)` orqali YANGI event loop yaratadi, lekin bu modul
    darajasidagi `_engine` butun jarayon davomida bitta (global) bo'lib
    qoladi. Oddiy pool (QueuePool) bilan birinchi vazifada ochilgan
    ulanish keyingi vazifada qayta ishlatilmoqchi bo'ladi — u esa
    allaqachon yopilgan eski event loop'ga bog'langan bo'ladi va
    asyncpg "attached to a different loop" xatosini beradi. NullPool
    har bir so'rov uchun yangi, hech narsaga bog'lanib qolmagan ulanish
    ochib, shu muammoni butunlay bartaraf etadi.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_dsn,
            echo=settings.environment == "development",
            poolclass=NullPool,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create (or return existing) session factory."""
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_engine()
        _async_session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI / Aiogram middleware.
    Usage:
        async with get_session() as session:
            ...
    or as middleware dependency.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables (for development / testing only). Prefer Alembic in production."""
    from app.db.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose engine on shutdown."""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None