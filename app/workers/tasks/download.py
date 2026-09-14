"""
Celery tasks for media download, progress updates and Telegram delivery.
Production-ready: long timeout, fallback, rate-limited progress, cleanup, robust error handling, Multi-Bot Pool.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramAPIError, TelegramRetryAfter
from aiogram.types import FSInputFile

from app.config import get_settings, get_next_bot_token
from app.core.cache.file_id_cache import get_file_id_cache, close_redis
from app.core.downloader.ytdlp_engine import YtDlpEngine
from app.core.downloader.cobalt import CobaltEngine
from app.core.downloader.pinterest_image import PinterestImageEngine
from app.core.downloader.social_og_fallback import SocialOgFallbackEngine
from app.db.session import get_session_factory
from app.db.repositories import DownloadRepository, UserRepository
from app.utils.link_parser import detect_platform
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

UPLOAD_TIMEOUT = 900  # 15 daqiqa (katta fayllar uchun)

# Progress xabarlarini tez-tez yangilab Telegram Rate Limit'ga tushmaslik uchun
LAST_UPDATE_TIME: dict[int, float] = {}


def _create_bot() -> Bot:
    """
    Worker ichida Bot yaratish.
    Multi-Account Bot Pool: Navbatdagi bot tokenidan foydalaniladi (Round-Robin).
    """
    token = get_next_bot_token()
    session = AiohttpSession(timeout=UPLOAD_TIMEOUT)
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


async def _update_progress(
    bot: Bot,
    chat_id: int,
    message_id: int,
    percent: float,
    status: str,
    force: bool = False,
):
    """Progress xabarnomasini rate limit bilan yangilash."""
    now = time.time()
    last_time = LAST_UPDATE_TIME.get(message_id, 0)

    if not force and (now - last_time < 1.5):
        return

    try:
        LAST_UPDATE_TIME[message_id] = now
        bar_len = 10
        filled = int(min(max(percent, 0), 100) / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        text = f"⬇️ Yuklanmoqda...\n\n{bar} {percent:.0f}%\n<i>{status}</i>"
        
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
    except Exception:
        pass


async def _send_media(
    bot: Bot,
    chat_id: int,
    file_path: Path,
    title: Optional[str],
    media_type: str,
    reply_to: Optional[int] = None,
):
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info("Sending media: %s (%.1f MB, type=%s)", file_path.name, file_size_mb, media_type)

    input_file = FSInputFile(file_path)
    caption = f"🎬 <b>{title}</b>" if title else None
    if caption and len(caption) > 1024:
        caption = caption[:1021] + "..."

    try:
        if media_type == "audio":
            msg = await bot.send_audio(
                chat_id=chat_id,
                audio=input_file,
                caption=caption,
                reply_to_message_id=reply_to,
                request_timeout=UPLOAD_TIMEOUT,
            )
            return msg.audio.file_id, msg.audio.file_unique_id
        elif media_type in {"image", "photo"}:
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=input_file,
                caption=caption,
                reply_to_message_id=reply_to,
                request_timeout=UPLOAD_TIMEOUT,
            )
            return msg.photo[-1].file_id, msg.photo[-1].file_unique_id
        else:
            msg = await bot.send_video(
                chat_id=chat_id,
                video=input_file,
                caption=caption,
                supports_streaming=True,
                reply_to_message_id=reply_to,
                request_timeout=UPLOAD_TIMEOUT,
            )
            return msg.video.file_id, msg.video.file_unique_id

    except TelegramNetworkError as e:
        logger.error("Telegram network error while sending (%.1f MB): %s", file_size_mb, e)
        raise RuntimeError(
            f"Telegram’ga yuborishda tarmoq xatosi (fayl ~{file_size_mb:.0f} MB). "
            f"Qayta urinib ko‘ring."
        ) from e
    except TelegramAPIError as e:
        logger.error("Telegram API error: %s", e)
        raise RuntimeError(f"Telegram xatosi: {e}") from e


async def _send_original_document(
    bot: Bot,
    chat_id: int,
    file_path: Path,
    title: Optional[str],
    reply_to: Optional[int] = None,
):
    """Asl (siqilmagan) faylni "hujjat" sifatida yuboradi.

    Telegram'ning oddiy send_photo metodi rasmni doim siqadi va qayta
    kodlaydi (uzun tomoni ~1280px gacha, JPEG). Pinterest rasm-fallback
    /originals/ (eng yuqori sifat) faylini olib kelgani uchun, uni
    send_document orqali yuborish siqishsiz, asl sifatda yetkazadi.
    """
    input_file = FSInputFile(file_path)
    caption = f"📁 Asl sifat — <b>{title}</b>" if title else "📁 Asl sifat"
    if len(caption) > 1024:
        caption = caption[:1021] + "..."
    try:
        msg = await bot.send_document(
            chat_id=chat_id,
            document=input_file,
            caption=caption,
            reply_to_message_id=reply_to,
            request_timeout=UPLOAD_TIMEOUT,
        )
        return msg.document.file_id, msg.document.file_unique_id
    except (TelegramNetworkError, TelegramAPIError) as e:
        # Asl fayl yuborilmasa ham, siqilgan variant allaqachon yetib borgan
        # bo'ladi — shuning uchun bu yerda faqat log qilamiz, jarayonni
        # butunlay to'xtatmaymiz.
        logger.warning("Original-quality document could not be sent: %s", e)
        return None, None


async def _cleanup(file_path: Optional[Path], message_id: Optional[int] = None):
    """Vaqtinchalik fayllar va kesh vaqtlarini tozalash."""
    if message_id and message_id in LAST_UPDATE_TIME:
        LAST_UPDATE_TIME.pop(message_id, None)

    if file_path and file_path.exists():
        try:
            file_path.unlink()
            logger.debug("Deleted temp file: %s", file_path)
        except Exception as e:
            logger.warning("Failed to delete %s: %s", file_path, e)


async def _process_download(
    *,
    url: str,
    user_id: int,
    chat_id: int,
    progress_message_id: int,
    quality: str = "720",
    audio_only: bool = False,
    platform: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    log_id: Optional[int] = None,
):
    settings = get_settings()
    bot = _create_bot()
    # Cookie faylini to'g'ri saytga moslashtirish uchun platformani yt-dlp
    # ishga tushishidan OLDIN aniqlaymiz (avval faqat fallback bosqichida
    # aniqlanardi, shu sabab yt-dlp har doim tasodifiy cookie bilan
    # ishlab, kerakli cookie mavjud bo'lsa ham undan foydalanmasdi).
    effective_platform = platform or detect_platform(url)
    engine = YtDlpEngine(platform=effective_platform)
    file_path: Optional[Path] = None

    loop = asyncio.get_running_loop()

    def progress_cb(percent: float, status: str):
        asyncio.run_coroutine_threadsafe(
            _update_progress(bot, chat_id, progress_message_id, percent, status),
            loop,
        )

    try:
        async with bot:
            # ---------- 1. yt-dlp ----------
            result = await engine.download(
                url=url,
                quality=quality,
                audio_only=audio_only,
                progress_callback=progress_cb,
            )

            # ---------- 2. Fallback zanjiri ----------
            if not result.success or not result.file_path:
                ytdlp_error = (result.error or "")

                # Pinterest uchun maxsus holat: agar yt-dlp "No video formats
                # found" desa, bu 99% hollarda pin oddiy RASM (video emas).
                # Cobalt ham Pinterest rasmlarini qo'llab-quvvatlamaydi, shuning
                # uchun uni behuda 9 ta serverga urib vaqt yo'qotmasdan, to'g'ridan
                # to'g'ri rasmni o'zimiz olib kelamiz.
                if effective_platform == "pinterest" and not audio_only:
                    logger.warning(
                        "yt-dlp failed for pinterest url=%s (%s) → image fallback",
                        url[:80], ytdlp_error[:120],
                    )
                    await _update_progress(bot, chat_id, progress_message_id, 10, "fallback: pinterest rasm", force=True)
                    pin_engine = PinterestImageEngine()
                    result = await pin_engine.download(
                        url=url,
                        quality=quality,
                        audio_only=audio_only,
                        progress_callback=progress_cb,
                    )

                # Instagram/Facebook uchun maxsus holat: post VIDEO emas,
                # RASM bo'lishi mumkin (yt-dlp faqat videoni tushunadi).
                # Cobalt'ga tashlash xavfli — Facebook uchun login-devor
                # sahifasidan Cobalt/generic scraper butunlay ALOQASIZ
                # (masalan reklama) videoni olib kelishi kuzatildi. Shu
                # sabab avval post'ning O'ZINING og:video/og:image
                # teglaridan to'g'ridan-to'g'ri media olib ko'ramiz.
                if effective_platform in {"instagram", "facebook"} and not audio_only:
                    logger.warning(
                        "yt-dlp failed for %s url=%s (%s) → OG fallback",
                        effective_platform, url[:80], ytdlp_error[:120],
                    )
                    await _update_progress(bot, chat_id, progress_message_id, 10, f"fallback: {effective_platform} rasm/video", force=True)
                    og_engine = SocialOgFallbackEngine(platform=effective_platform)
                    result = await og_engine.download(
                        url=url,
                        quality=quality,
                        audio_only=audio_only,
                        progress_callback=progress_cb,
                    )

                # Agar hali ham muvaffaqiyatsiz bo'lsa (yoki boshqa platforma
                # bo'lsa), umumiy Cobalt fallback'ga o'tamiz — bu haqiqiy video
                # bo'lib, faqat yt-dlp bot-blok/format muammosiga uchragan
                # holatlarni qamrab oladi.
                #
                # Facebook BUNDAN MUSTASNO: Cobalt/generic scraper login
                # talab qiladigan Facebook sahifalarida postga ALOQASI
                # YO'Q video (masalan tasodifiy reklama/viktorina klipi)
                # qaytarganligi kuzatildi — bu foydalanuvchiga butunlay
                # noto'g'ri kontent yuborish degani, "umuman yuklanmadi"
                # xabaridan ham yomonroq. Shuning uchun Facebook uchun bu
                # bosqich butunlay o'tkazib yuboriladi.
                if (
                    (not result.success or not result.file_path)
                    and effective_platform != "facebook"
                ):
                    logger.warning("yt-dlp failed for url=%s → Cobalt fallback", url[:80])
                    await _update_progress(bot, chat_id, progress_message_id, 10, "fallback: cobalt", force=True)
                    cobalt = CobaltEngine()
                    result = await cobalt.download(
                        url=url,
                        quality=quality,
                        audio_only=audio_only,
                        progress_callback=progress_cb,
                    )

            # ---------- 3. Barchasi muvaffaqiyatsiz ----------
            if not result.success or not result.file_path:
                error_text = (result.error or "Noma’lum xatolik")[:300]
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text=f"❌ Yuklab bo‘lmadi:\n<code>{error_text}</code>",
                )
                if log_id:
                    factory = get_session_factory()
                    async with factory() as session:
                        repo = DownloadRepository(session)
                        await repo.update_status(log_id, status="failed", error_message=error_text)
                        await session.commit()
                return

            file_path = result.file_path
            filesize = result.filesize or file_path.stat().st_size
            filesize_mb = filesize / (1024 * 1024)

            # ---------- 4. Hajm limiti ----------
            max_bytes = settings.max_file_size_mb * 1024 * 1024
            if filesize > max_bytes:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text=(
                        f"❌ Fayl juda katta ({filesize_mb:.0f} MB).\n"
                        f"Limit: {settings.max_file_size_mb} MB.\n"
                        f"Qisqaroq video yoki pastroq sifat tanlang."
                    ),
                )
                return

            # ---------- 5. Telegram'ga yuborish ----------
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text=f"📤 Telegram’ga yuborilmoqda... ({filesize_mb:.1f} MB)",
            )

            file_id, file_unique_id = await _send_media(
                bot=bot,
                chat_id=chat_id,
                file_path=file_path,
                title=result.title,
                media_type=result.media_type,
                reply_to=reply_to_message_id,
            )

            # Pinterest rasm bo'lsa — siqilgan (photo) variatdan tashqari,
            # xuddi shu faylni asl (siqilmagan) sifatda ham "hujjat" sifatida
            # yuboramiz, foydalanuvchi ikkalasidan birini tanlab ololsin.
            original_file_id: Optional[str] = None
            original_file_unique_id: Optional[str] = None
            if result.media_type in {"image", "photo"} and (platform == "pinterest" or result.platform == "pinterest"):
                original_file_id, original_file_unique_id = await _send_original_document(
                    bot=bot,
                    chat_id=chat_id,
                    file_path=file_path,
                    title=result.title,
                    reply_to=reply_to_message_id,
                )

            # Progress xabarni o‘chirish
            try:
                await bot.delete_message(chat_id, progress_message_id)
            except Exception:
                pass

            # ---------- 6. Cache + DB ----------
            cache = await get_file_id_cache()
            extra = None
            if original_file_id:
                extra = {
                    "original_file_id": original_file_id,
                    "original_file_unique_id": original_file_unique_id,
                }
            await cache.set(
                url=url,
                file_id=file_id,
                file_unique_id=file_unique_id,
                media_type=result.media_type,
                quality=quality,
                platform=platform or result.platform,
                extra=extra,
            )

            factory = get_session_factory()
            async with factory() as session:
                download_repo = DownloadRepository(session)
                user_repo = UserRepository(session)

                if log_id:
                    await download_repo.update_status(
                        log_id=log_id,
                        status="success",
                        file_id=file_id,
                        file_unique_id=file_unique_id,
                        file_size=filesize,
                        was_cached=False,
                    )

                await user_repo.increment_daily_download(user_id)
                await session.commit()

            logger.info(
                "SUCCESS user=%s size=%.1fMB url=%s",
                user_id,
                filesize_mb,
                url[:60],
            )

    except Exception as e:
        logger.exception("Task failed for url=%s", url)
        err_msg = str(e)[:300]
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text=f"❌ Xatolik yuz berdi:\n<code>{err_msg}</code>",
            )
        except Exception:
            pass

        if log_id:
            try:
                factory = get_session_factory()
                async with factory() as session:
                    repo = DownloadRepository(session)
                    await repo.update_status(log_id, status="failed", error_message=err_msg)
                    await session.commit()
            except Exception:
                pass
    finally:
        await _cleanup(file_path, progress_message_id)
        # Redis clientni shu loop yopilishidan oldin yopamiz — Celery
        # worker har bir vazifa uchun asyncio.run() bilan yangi event loop
        # ochadi, global (keshlangan) Redis ulanishi esa eski loop'ga
        # bog'lanib qolib, keyingi vazifada xuddi database bilan bo'lgani
        # kabi "boshqa loop'ga bog'langan" xatosini berishi mumkin edi.
        try:
            await close_redis()
        except Exception:
            pass


@celery_app.task(
    bind=True,
    name="app.workers.tasks.download.process_download",
    max_retries=1,
    soft_time_limit=1200,   # 20 daqiqa soft
    time_limit=1500,        # 25 daqiqa hard
)
def process_download(
    self,
    url: str,
    user_id: int,
    chat_id: int,
    progress_message_id: int,
    quality: str = "720",
    audio_only: bool = False,
    platform: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    log_id: Optional[int] = None,
):
    try:
        asyncio.run(
            _process_download(
                url=url,
                user_id=user_id,
                chat_id=chat_id,
                progress_message_id=progress_message_id,
                quality=quality,
                audio_only=audio_only,
                platform=platform,
                reply_to_message_id=reply_to_message_id,
                log_id=log_id,
            )
        )
    except Exception as exc:
        logger.exception("Celery task execution failed: %s", exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10)