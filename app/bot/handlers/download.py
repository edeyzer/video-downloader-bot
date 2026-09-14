"""
Main download handler.
Accepts links, shows choice buttons (Video / Audio) for videos,
directly downloads images for Pinterest, checks cache, enforces limits.
"""

import logging
import re

import httpx
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.cache.callback_store import resolve_token, store_url
from app.core.cache.file_id_cache import get_file_id_cache
from app.core.downloader.social_og_fallback import SocialOgFallbackEngine
from app.db.repositories import UserRepository, DownloadRepository
from app.utils.link_parser import parse_link
from app.workers.tasks.download import process_download

logger = logging.getLogger(__name__)
router = Router(name="download")

# Instagram/Facebook'da URL YO'LINING O'ZIDA "bu 100% VIDEO" degan aniq
# belgi bo'lgan holatlar. HTML skrepting (embed sahifa / og: teglar)
# anti-bot himoyasi, cookie yo'qligi yoki Instagram/Facebook sahifa
# tuzilishi o'zgarishi tufayli har doim ham ishonchli emas — lekin URL
# manzilining o'zi hech qachon yolg'on gapirmaydi: "/reel/", "/reels/"
# (Instagram/Facebook Reels), "/videos/", "/watch" (Facebook video) —
# bularning barchasi har doim VIDEO, hech qachon oddiy rasm emas. Shu
# sabab bunday linklar uchun OG-probe'ga umuman murojaat qilinmaydi va
# "rasm" deb noto'g'ri xulosa chiqarish imkoniyati butunlay yo'q qilinadi.
_DEFINITELY_VIDEO_PATH_RE = re.compile(
    r"/(?:reels?|videos?|watch|tv)(?:[/?]|$)",
    re.IGNORECASE,
)


def _is_definitely_video_url(url: str) -> bool:
    return bool(_DEFINITELY_VIDEO_PATH_RE.search(url))


# Facebook'ning "share/r/<id>", "s/<id>" yoki fb.watch qisqa havolalari
# o'z yo'lida HECH QACHON "/reel/" yoki "/videos/" so'zini ko'rsatmaydi —
# bu faqat serverga borib, redirect qilingandan KEYIN ma'lum bo'ladi. Shu
# sabab yuqoridagi _is_definitely_video_url() bunday havolalarni hech
# qachon aniqlay olmasdi, va bot Facebook uchun cookie fayli yo'qligi
# tufayli deyarli doim "rasm" deb xulosa chiqaradigan OG-probe bosqichiga
# tushib qolardi. Yechim: shunday havolalarni PROBE qilishdan oldin
# haqiqiy (redirect qilingan) manziliga ochib olamiz.
_FACEBOOK_SHORT_LINK_RE = re.compile(
    r"facebook\.com/(?:share/|s/)|fb\.watch/",
    re.IGNORECASE,
)

_REDIRECT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


async def _resolve_facebook_share_url(url: str) -> str:
    """Facebook qisqa/share havolasini haqiqiy post/reel manziliga ochadi."""
    if not _FACEBOOK_SHORT_LINK_RE.search(url):
        return url
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=8.0, headers=_REDIRECT_HEADERS
        ) as client:
            async with client.stream("GET", url) as resp:
                final_url = str(resp.url)
                return final_url or url
    except Exception as e:
        logger.debug("Facebook share-link resolve failed for %s: %s", url[:80], e)
        return url


# Video/Audio tanlovi endi 4 ta variant beradi: 3 ta video sifat (asl —
# eng yuqori, va undan taxminan 2x va 4x pastroq) + Audio (MP3). "mode"
# qiymati callback_data'da to'g'ridan-to'g'ri yt-dlp balandlik (height)
# formatiga mos keladi ("best" yoki "720"/"480"), shuning uchun uni
# process_download_choice'da qayta talqin qilishning hojati yo'q.
_QUALITY_BUTTONS = [
    ("best", "🎬 Original sifat"),
    ("720", "🎥 O‘rtacha (720p)"),
    ("480", "📽 Past (480p)"),
]


def get_choice_keyboard(token: str) -> InlineKeyboardMarkup:
    """Video sifatlari + Audio tanlash tugmalari.

    Tugma callback_data'siga URL emas, faqat qisqa token joylanadi —
    Telegram callback_data 64 baytdan oshsa so'rovni butunlay rad etadi
    (BUTTON_DATA_INVALID), uzun (masalan utm/si parametrli) linklar esa
    64 baytdan osha oladi.

    MUHIM: shu tugmalar bosilgandan keyin ham xabar (va tugmalar)
    o'chirilmaydi/tahrirlanmaydi (process_download_choice'ga qarang) —
    shu sabab foydalanuvchi bitta linkdan bir nechta formatni (masalan
    avval Audio, keyin Original video) ketma-ket, linkni qayta
    yubormasdan tanlab olishi mumkin.
    """
    quality_row = [
        InlineKeyboardButton(text=label, callback_data=f"dl:{mode}:{token}")
        for mode, label in _QUALITY_BUTTONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        quality_row,
        [InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data=f"dl:audio:{token}")],
    ])


@router.message(F.text.regexp(r"(https?://[^\s]+|www\.[^\s]+)"))
async def handle_link(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user_repo: UserRepository,
    download_repo: DownloadRepository,
):
    if not message.text or not message.from_user:
        return

    parsed = parse_link(message.text.strip())
    if not parsed:
        return

    # Facebook "share/r/..." / fb.watch qisqa havolalarini eng boshida
    # haqiqiy (redirect qilingan) manziliga ochamiz — shundan keyingina
    # keshlash, sifat aniqlash va yuklab olishning BARCHASI haqiqiy,
    # to'liq URL bilan ishlaydi.
    if parsed.platform == "facebook":
        parsed.url = await _resolve_facebook_share_url(parsed.url)

    user = message.from_user
    settings = get_settings()

    try:
        db_user, _ = await user_repo.get_or_create(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            language_code=user.language_code,
        )

        if db_user.is_blocked:
            await message.answer("🚫 Sizning hisobingiz bloklangan.")
            return

        # Kunlik limit tekshiruvi
        if not db_user.is_premium:
            if db_user.daily_downloads >= settings.free_daily_limit:
                await message.answer(
                    f"⚠️ Kunlik tekin limitingiz tugadi ({settings.free_daily_limit} ta).\n"
                    f"Cheklovsiz yuklash uchun /premium obunasini xarid qiling."
                )
                return

        # Smart Cache tekshiruvi
        cache = await get_file_id_cache()
        cached = await cache.get(parsed.url)

        if cached and cached.get("file_id"):
            try:
                media_type = cached.get("media_type", "video")
                file_id = cached["file_id"]

                if media_type == "audio":
                    await message.answer_audio(
                        audio=file_id,
                        caption="⚡ Tezkor yuklab olindi (Cache)",
                        reply_to_message_id=message.message_id
                    )
                elif media_type in {"photo", "image"}:
                    await message.answer_photo(
                        photo=file_id,
                        caption="⚡ Tezkor yuklab olindi (Cache)",
                        reply_to_message_id=message.message_id
                    )
                    # Pinterest uchun asl (siqilmagan) fayl ham keshlangan
                    # bo'lsa, uni ham hujjat sifatida yuboramiz.
                    original_file_id = cached.get("original_file_id")
                    if original_file_id:
                        try:
                            await message.answer_document(
                                document=original_file_id,
                                caption="📁 Asl sifat (Cache)",
                                reply_to_message_id=message.message_id
                            )
                        except Exception as e:
                            logger.warning("Cached original document invalid: %s", e)
                else:
                    await message.answer_video(
                        video=file_id,
                        caption="⚡ Tezkor yuklab olindi (Cache)",
                        supports_streaming=True,
                        reply_to_message_id=message.message_id
                    )

                await download_repo.create(
                    user_id=user.id,
                    url=parsed.url,
                    platform=parsed.platform,
                    media_type=media_type,
                    quality="best",
                    status="cached",
                )
                await user_repo.increment_daily_download(user.id)
                await session.commit()
                return
            except Exception as e:
                logger.warning("Cached file_id invalid or expired: %s", e)
                await cache.delete(parsed.url)

        # ========== Pinterest uchun maxsus (Video/Audio chiqmasin) ==========
        if parsed.platform and parsed.platform.lower() == "pinterest":
            log = await download_repo.create(
                user_id=user.id,
                url=parsed.url,
                platform="pinterest",
                media_type="image",
                quality="best",
                status="pending",
            )
            await session.commit()

            progress_msg = await message.answer(
                "🖼 Pinterest media yuklanmoqda...",
                reply_to_message_id=message.message_id
            )

            process_download.delay(
                url=parsed.url,
                user_id=user.id,
                chat_id=message.chat.id,
                progress_message_id=progress_msg.message_id,
                quality="best",
                audio_only=False,
                platform="pinterest",
                reply_to_message_id=message.message_id,
                log_id=log.id,
            )
            return
        # ==================================================================

        # ========== Instagram/Facebook: RASM bo'lsa tugmasiz yuborish ==========
        # Root cause this fixes: bot avval "Video / Audio" tugmalarini har
        # doim ko'rsatardi, chunki link turi (rasm/video) faqat yuklashga
        # urinib ko'rgandan KEYIN ma'lum bo'lardi. Foydalanuvchi uchun bu
        # g'alati edi — rasm linkiga ham "audio kerakmi?" deb so'ralardi.
        # Endi link kelgan zahoti (hali tugma chiqmasdan) tezkor tekshirib
        # ko'ramiz: agar bu ANIQ rasm ekani bilinsa, hech qanday savolsiz
        # to'g'ridan-to'g'ri yuklab yuboramiz — video bo'lsa (yoki turi
        # noaniq bo'lib qolsa), eski Video/Audio tanlovi ko'rsatiladi.
        if parsed.platform in {"instagram", "facebook"}:
            if _is_definitely_video_url(parsed.url):
                # URL yo'lining o'zi ("/reel/", "/videos/", "/watch"...)
                # bu 100% video ekanini ko'rsatadi — OG-probe'ga
                # murojaat qilinmaydi, to'g'ridan-to'g'ri pastdagi
                # Video/Audio tanlovi ko'rsatiladi (kod davom etadi).
                kind = "video"
            else:
                probe_engine = SocialOgFallbackEngine(platform=parsed.platform)
                kind = await probe_engine.probe_kind(parsed.url)

            if kind == "image":
                log = await download_repo.create(
                    user_id=user.id,
                    url=parsed.url,
                    platform=parsed.platform,
                    media_type="image",
                    quality="best",
                    status="pending",
                )
                await session.commit()

                progress_msg = await message.answer(
                    "🖼 Rasm yuklanmoqda...",
                    reply_to_message_id=message.message_id
                )

                process_download.delay(
                    url=parsed.url,
                    user_id=user.id,
                    chat_id=message.chat.id,
                    progress_message_id=progress_msg.message_id,
                    quality="best",
                    audio_only=False,
                    platform=parsed.platform,
                    reply_to_message_id=message.message_id,
                    log_id=log.id,
                )
                return
        # ========================================================================

        # ========== Boshqa platformalar uchun Video / Audio tanlovi ==========
        token = await store_url(parsed.url)
        await message.answer(
            f"📥 <b>Yuklab olish</b>\n\n"
            f"Platforma: <b>{parsed.platform or 'nomaʼlum'}</b>\n"
            f"Qanday formatda yuklab olmoqchisiz?",
            reply_markup=get_choice_keyboard(token),
            reply_to_message_id=message.message_id
        )

    except Exception as e:
        await session.rollback()
        logger.exception("Error handling link: %s", e)
        await message.answer("⚠️ Ishlov berishda xatolik yuz berdi. Qaytadan urinib ko‘ring.")


@router.callback_query(F.data.startswith("dl:"))
async def process_download_choice(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user_repo: UserRepository,
    download_repo: DownloadRepository,
):
    """Sifat yoki Audio tugmasi bosilganda ishlaydi.

    MUHIM: asl tanlov xabari (va uning tugmalari) BU YERDA HECH QACHON
    tahrirlanmaydi yoki o'chirilmaydi — progress uchun har doim YANGI
    xabar yuboriladi. Avval kod callback.message.edit_text(...) chaqirar
    edi, bu esa Telegram'da xabarning inline tugmalarini ham olib
    tashlardi (chunki editMessageText'da reply_markup qayta
    yuborilmasa, mavjud klaviatura o'chib ketadi). Natijada foydalanuvchi
    bitta formatni (masalan Audio) tanlagandan keyin, boshqa formatni
    (masalan Video) olish uchun linkni QAYTADAN yuborishga majbur bo'lardi.
    Endi tugmalar doim joyida qoladi va token 30 daqiqa amal qiladi
    (callback_store.py), shuning uchun bitta xabardan istalgan nechta
    formatni ketma-ket bosib olish mumkin.
    """
    if not callback.data or not callback.from_user or not callback.message:
        await callback.answer()
        return

    try:
        _, mode, token = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Noto‘g‘ri maʼlumot", show_alert=True)
        return

    if mode not in {"best", "720", "480", "audio"}:
        await callback.answer("Noto‘g‘ri maʼlumot", show_alert=True)
        return

    url = await resolve_token(token)
    if not url:
        await callback.answer(
            "⏱ Havola muddati tugagan. Iltimos, linkni qayta yuboring.",
            show_alert=True,
        )
        return

    audio_only = mode == "audio"
    quality = "best" if mode == "audio" else mode
    user = callback.from_user
    settings = get_settings()

    db_user, _ = await user_repo.get_or_create(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
    )

    if not db_user.is_premium and db_user.daily_downloads >= settings.free_daily_limit:
        await callback.answer("Kunlik limit tugadi!", show_alert=True)
        return

    log = await download_repo.create(
        user_id=user.id,
        url=url,
        platform=None,
        media_type="audio" if audio_only else "video",
        quality=quality,
        status="pending",
    )
    await session.commit()

    label = "🎵 Audio" if audio_only else f"🎬 Video ({'original' if quality == 'best' else quality + 'p'})"
    progress_msg = await callback.message.answer(
        f"⏳ {label} qabul qilindi, yuklanmoqda...",
        reply_to_message_id=callback.message.message_id,
    )

    process_download.delay(
        url=url,
        user_id=user.id,
        chat_id=callback.message.chat.id,
        progress_message_id=progress_msg.message_id,
        quality=quality,
        audio_only=audio_only,
        platform=None,
        reply_to_message_id=callback.message.reply_to_message.message_id if callback.message.reply_to_message else None,
        log_id=log.id,
    )

    await callback.answer("✅ Qabul qilindi!")
    logger.info("Choice: user=%s mode=%s url=%s", user.id, mode, url[:60])