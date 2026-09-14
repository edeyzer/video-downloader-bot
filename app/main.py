"""
Main entrypoint for the Video Downloader Telegram Bot (Aiogram 3).
Production-ready version with all routers, middlewares and graceful shutdown.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis

from app.config import get_settings
from app.core.cache.file_id_cache import close_redis, get_redis
from app.db.session import close_db

# Middlewares
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.bot.middlewares.subscription import SubscriptionMiddleware

# Handlers / Routers
from app.bot.handlers import (
    start,
    download,
    audio,
    trim,
    premium,
    payments,
    admin,
    inline,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bot factory
# ---------------------------------------------------------------------------
async def create_bot() -> Bot:
    """Create Bot instance. Supports optional Local Telegram Bot API Server."""
    settings = get_settings()

    token = settings.bot_token or (settings.all_bot_tokens[0] if settings.all_bot_tokens else None)
    if not token:
        raise ValueError("BOT_TOKEN ko'rsatilmadi!")

    session = None
    if settings.telegram_api_server:
        try:
            from aiogram.client.telegram import TelegramAPIServer

            local_server = TelegramAPIServer.from_base(settings.telegram_api_server)
            session = AiohttpSession(api=local_server)
            logger.info(
                "Using Local Telegram Bot API Server: %s",
                settings.telegram_api_server,
            )
        except Exception as e:
            logger.warning(
                "Could not configure Local Bot API (%s), falling back to official API",
                e,
            )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    return bot


# ---------------------------------------------------------------------------
# Dispatcher factory
# ---------------------------------------------------------------------------
async def create_dispatcher() -> Dispatcher:
    """
    Create Dispatcher with:
    - Redis FSM storage
    - All middlewares
    - All routers in correct order
    """
    settings = get_settings()

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis)

    dp = Dispatcher(storage=storage)

    # ========== Middlewares (order matters) ==========
    # 1. Database session + repositories injection
    dp.update.middleware(DbSessionMiddleware())

    # 2. Rate limiting / anti-spam
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    # 3. Force subscription check
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())

    # ========== Routers (order matters) ==========
    dp.include_router(start.router)
    dp.include_router(download.router)
    dp.include_router(audio.router)
    dp.include_router(trim.router)
    dp.include_router(premium.router)
    dp.include_router(payments.router)   # Stars invoices + successful_payment
    dp.include_router(admin.router)
    dp.include_router(inline.router)

    return dp


# ---------------------------------------------------------------------------
# Startup / Shutdown hooks
# ---------------------------------------------------------------------------
async def on_startup(bot: Bot) -> None:
    """Actions performed once when the bot starts."""
    settings = get_settings()
    me = await bot.get_me()

    # ========== Bot Commands Menu (/ bosganda chiqadigan ro'yxat) ==========
    commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="menu", description="Barcha xizmatlar"),
        BotCommand(command="help", description="Yordam"),
        BotCommand(command="mp3", description="Audio (MP3) yuklash"),
        BotCommand(command="premium", description="Premium obuna"),
    ]
    await bot.set_my_commands(commands)
    logger.info("Bot commands menu set successfully")
    # ======================================================================

    logger.info("=" * 60)
    logger.info("Bot started: @%s (id=%s)", me.username, me.id)
    logger.info("Environment : %s", settings.environment)
    logger.info("Admins      : %s", settings.admin_ids)
    logger.info(
        "Database    : %s",
        settings.database_dsn.replace(settings.postgres_password, "***"),
    )
    logger.info("Redis       : %s", settings.redis_url)

    # Warm-up Redis
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("Redis connection: OK")
    except Exception as e:
        logger.error("Redis connection FAILED: %s", e)

    logger.info("=" * 60)


async def on_shutdown(bot: Bot) -> None:
    """Graceful shutdown: close Redis, DB engine and bot session."""
    logger.info("Graceful shutdown initiated...")
    try:
        await close_redis()
        logger.info("Redis closed")
    except Exception as e:
        logger.warning("Error closing Redis: %s", e)

    try:
        await close_db()
        logger.info("Database engine disposed")
    except Exception as e:
        logger.warning("Error closing DB: %s", e)

    try:
        await bot.session.close()
        logger.info("Bot session closed")
    except Exception as e:
        logger.warning("Error closing bot session: %s", e)

    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------
async def main() -> None:
    """Application entrypoint."""
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level.upper())

    bot = await create_bot()
    dp = await create_dispatcher()

    # Register lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        # Ensure we are in polling mode
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Starting long-polling...")
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,  # we close it ourselves in on_shutdown
        )
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by signal (KeyboardInterrupt / SystemExit)")