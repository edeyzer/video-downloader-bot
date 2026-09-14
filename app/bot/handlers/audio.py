"""
Audio extraction handler (/mp3).
"""

import logging

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories import UserRepository
from app.utils.link_parser import parse_link
from app.workers.tasks.download import process_download

logger = logging.getLogger(__name__)
router = Router(name="audio")


@router.message(Command("mp3"))
async def cmd_mp3(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user_repo: UserRepository,
):
    text = message.text or ""
    url = None

    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        parsed = parse_link(parts[1])
        if parsed:
            url = parsed.url

    if not url and message.reply_to_message and message.reply_to_message.text:
        parsed = parse_link(message.reply_to_message.text)
        if parsed:
            url = parsed.url

    if not url:
        await message.answer(
            "🎵 <b>MP3 yuklab olish</b>\n\n"
            "Ishlatish:\n"
            "• <code>/mp3 https://...</code>\n"
            "• yoki video linkiga reply qilib <code>/mp3</code> yuboring."
        )
        return

    await _dispatch_audio(message, bot, session, user_repo, url)


@router.callback_query(F.data.startswith("mp3:"))
async def callback_mp3(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🎵 To‘liq link bilan qayta yuboring:\n<code>/mp3 https://...</code>"
    )


async def _dispatch_audio(message, bot, session, user_repo, url: str):
    user = message.from_user
    if not user:
        return

    settings = get_settings()
    db_user, _ = await user_repo.get_or_create(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
    )

    if db_user.is_blocked:
        await message.answer("🚫 Hisobingiz bloklangan.")
        return

    if not db_user.is_premium and db_user.daily_downloads >= settings.free_daily_limit:
        await message.answer("⚠️ Kunlik limit tugadi. Premium uchun /premium")
        return

    progress_msg = await message.answer("🎵 Audio ajratilmoqda...")

    process_download.delay(
        url=url,
        user_id=user.id,
        chat_id=message.chat.id,
        progress_message_id=progress_msg.message_id,
        quality="720",
        audio_only=True,
        platform=None,
        reply_to_message_id=message.message_id,
        log_id=None,
    )