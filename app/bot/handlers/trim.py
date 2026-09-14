"""
Video trim handler (Premium only).
"""

import logging
from pathlib import Path

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.media.ffmpeg import trim_video, parse_timecode
from app.db.repositories import UserRepository
from app.utils.link_parser import parse_link

logger = logging.getLogger(__name__)
router = Router(name="trim")


@router.message(Command("trim"))
async def cmd_trim(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user_repo: UserRepository,
):
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

    if not db_user.is_premium:
        await message.answer(
            "✂️ Video qirqish faqat <b>Premium</b> foydalanuvchilar uchun.\n"
            "Batafsil: /premium"
        )
        return

    text = (message.text or "").strip()
    parts = text.split()

    if len(parts) < 3:
        await message.answer(
            "✂️ <b>Video qirqish</b>\n\n"
            "Ishlatish:\n"
            "1. Videoga <b>reply</b> qilib:\n"
            "   <code>/trim 0:30 1:45</code>\n\n"
            "2. Yoki link bilan:\n"
            "   <code>/trim 0:30 1:45 https://...</code>\n\n"
            "Vaqt formati: <code>SS</code>, <code>MM:SS</code> yoki <code>HH:MM:SS</code>"
        )
        return

    start = parse_timecode(parts[1])
    end = parse_timecode(parts[2])

    if not start or not end:
        await message.answer("❌ Vaqt formati noto‘g‘ri. Masalan: <code>0:30</code> yoki <code>90</code>")
        return

    # Reply to existing video
    if message.reply_to_message and message.reply_to_message.video:
        status = await message.answer("✂️ Qirqilmoqda...")
        try:
            file = message.reply_to_message.video
            file_info = await bot.get_file(file.file_id)
            download_dir = Path(settings.download_dir)
            download_dir.mkdir(parents=True, exist_ok=True)
            local_path = download_dir / f"trim_src_{file.file_unique_id}.mp4"

            await bot.download_file(file_info.file_path, local_path)

            ok, out_path, err = await trim_video(local_path, start=start, end=end)
            if not ok or not out_path:
                await status.edit_text(f"❌ Qirqish muvaffaqiyatsiz:\n<code>{err[:200]}</code>")
                local_path.unlink(missing_ok=True)
                return

            await status.edit_text("📤 Yuborilmoqda...")
            await message.answer_video(
                video=FSInputFile(out_path),
                caption=f"✂️ {start} → {end}",
                supports_streaming=True,
                reply_to_message_id=message.message_id,
            )
            await status.delete()
            local_path.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
        except Exception as e:
            logger.exception("Trim failed")
            await status.edit_text(f"❌ Xatolik: {e}")
        return

    # URL case
    url = None
    if len(parts) >= 4:
        parsed = parse_link(parts[3])
        if parsed:
            url = parsed.url

    if not url:
        await message.answer("❌ Link topilmadi. Videoga reply qiling yoki link qo‘shing.")
        return

    await message.answer(
        f"⏳ Linkdan yuklab, keyin qirqiladi...\n"
        f"Vaqt oralig‘i: <code>{start} → {end}</code>\n\n"
        f"Hozircha to‘liq video yuklanadi. "
        f"Yuklangan videoga reply qilib /trim ishlating."
    )