"""
Premium subscription handlers.
"""

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories import UserRepository, SubscriptionRepository

logger = logging.getLogger(__name__)
router = Router(name="premium")


# Telegram Stars (XTR) tariflari uchun tugmalar
def premium_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ 7 kun — 80 Stars", callback_data="buy_premium:7_days")],
        [InlineKeyboardButton(text="⭐ 1 oy — 250 Stars", callback_data="buy_premium:30_days")],
        [InlineKeyboardButton(text="👑 1 yil — 2000 Stars", callback_data="buy_premium:365_days")],
    ])


@router.message(Command("premium"))
async def cmd_premium(
    message: Message,
    session: AsyncSession,
    user_repo: UserRepository,
    sub_repo: SubscriptionRepository,
):
    user = message.from_user
    if not user:
        return

    db_user, _ = await user_repo.get_or_create(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
    )

    settings = get_settings()
    active = await sub_repo.get_active_by_user(user.id)

    if db_user.is_premium or active:
        until = db_user.premium_until
        until_text = until.strftime("%Y-%m-%d %H:%M") if until else "cheksiz"
        text = (
            f"⭐ <b>Premium faol</b>\n\n"
            f"Holat: ✅ Premium\n"
            f"Amal qilish muddati: <b>{until_text}</b>\n\n"
            f"<b>Imkoniyatlar:</b>\n"
            f"• Cheksiz yuklash\n"
            f"• 1080p / 4K sifat\n"
            f"• Navbatsiz yuklash\n"
            f"• Video qirqish (trim)\n"
            f"• MP3 ajratish"
        )
        await message.answer(text)
        return

    text = (
        f"⭐ <b>Premium obuna</b>\n\n"
        f"Bepul tarif:\n"
        f"• Kuniga {settings.free_daily_limit} ta yuklash\n"
        f"• Maksimal {settings.free_max_quality}p\n\n"
        f"<b>Premium bilan:</b>\n"
        f"• ✅ Cheksiz yuklash\n"
        f"• ✅ 1080p va undan yuqori\n"
        f"• ✅ Navbatsiz yuklash\n"
        f"• ✅ Video qirqish\n"
        f"• ✅ Tezkor support\n\n"
        f"Quyidagi tariflardan birini tanlang (Telegram Stars orqali):"
    )
    await message.answer(text, reply_markup=premium_keyboard())


# ESKI TO'LOV HANDLERI OLIB TASHALNDI. 
# Endi to'lov jarayonini app/bot/handlers/payments.py fayli boshqaradi.


@router.message(Command("grant_premium"))
async def cmd_grant_premium(
    message: Message,
    session: AsyncSession,
    user_repo: UserRepository,
    sub_repo: SubscriptionRepository,
):
    """Admin-only: /grant_premium <user_id> [days]"""
    settings = get_settings()
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Ishlatish: <code>/grant_premium user_id [days]</code>")
        return

    try:
        target_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        await message.answer("user_id va days son bo‘lishi kerak.")
        return

    expires = None
    if days > 0:
        expires = datetime.now(timezone.utc) + timedelta(days=days)

    await user_repo.update_premium_status(target_id, is_premium=True, premium_until=expires)
    await sub_repo.deactivate_all_for_user(target_id)
    await sub_repo.create(
        user_id=target_id,
        plan="lifetime" if days == 0 else f"{days}d",
        expires_at=expires,
        payment_provider="manual",
        is_active=True,
    )
    await session.commit()

    until_text = "cheksiz" if not expires else expires.strftime("%Y-%m-%d")
    await message.answer(f"✅ User {target_id} ga Premium berildi ({until_text})")