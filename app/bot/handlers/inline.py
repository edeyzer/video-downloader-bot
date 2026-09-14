"""
Inline mode — @BotUsername <link>
"""

import logging
from uuid import uuid4

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedAudio,
    InputTextMessageContent,
    ChosenInlineResult,
)

from app.core.cache.file_id_cache import get_file_id_cache
from app.utils.link_parser import parse_link

logger = logging.getLogger(__name__)
router = Router(name="inline")


@router.inline_query()
async def inline_search(query: InlineQuery):
    text = (query.query or "").strip()
    results = []

    if not text:
        results.append(InlineQueryResultArticle(
            id=str(uuid4()),
            title="Link yuboring",
            description="YouTube, Instagram, TikTok va boshqa platformalar linkini yozing",
            input_message_content=InputTextMessageContent(
                message_text="🔗 Bot orqali video yuklab olish uchun linkni yuboring."
            ),
        ))
        await query.answer(results, cache_time=5, is_personal=True)
        return

    parsed = parse_link(text)
    if not parsed:
        results.append(InlineQueryResultArticle(
            id=str(uuid4()),
            title="Qo‘llab-quvvatlanmaydigan link",
            description="YouTube, Instagram, TikTok, Pinterest...",
            input_message_content=InputTextMessageContent(
                message_text="❌ Bu platforma hozircha qo‘llab-quvvatlanmaydi."
            ),
        ))
        await query.answer(results, cache_time=10, is_personal=True)
        return

    cache = await get_file_id_cache()
    cached = await cache.get(parsed.url)

    if cached and cached.get("file_id"):
        media_type = cached.get("media_type", "video")
        file_id = cached["file_id"]
        if media_type == "audio":
            results.append(InlineQueryResultCachedAudio(
                id=str(uuid4()),
                audio_file_id=file_id,
                caption=f"🎵 {parsed.platform} | Cache’dan",
            ))
        else:
            results.append(InlineQueryResultCachedVideo(
                id=str(uuid4()),
                video_file_id=file_id,
                title=f"⚡ {parsed.platform.title()} — Cache",
                description="Darhol yuborish (cache)",
                caption=f"🎬 {parsed.platform} | Cache’dan",
            ))
    else:
        results.append(InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"⬇️ {parsed.platform.title()} yuklab olish",
            description="Botga o‘tib yuklab oling (birinchi marta)",
            input_message_content=InputTextMessageContent(
                message_text=(
                    f"🔗 {parsed.url}\n\n"
                    f"Video hali cache’da yo‘q.\n"
                    f"Botga o‘tib linkni yuboring — keyin inline orqali ham ishlaydi."
                )
            ),
        ))

    await query.answer(results, cache_time=30, is_personal=True)


@router.chosen_inline_result()
async def chosen_inline(result: ChosenInlineResult):
    logger.debug("Inline chosen by %s", result.from_user.id if result.from_user else "?")