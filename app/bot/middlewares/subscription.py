"""
Force-subscription middleware.
Checks that the user is subscribed to required channels before allowing any action.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, CallbackQuery, TelegramObject, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import get_settings

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseMiddleware):
    """
    If FORCE_SUB_CHANNELS is configured, user must be a member of all of them.
    Otherwise the middleware answers with a "Subscribe" keyboard and blocks the handler.
    """

    def __init__(self, channels: Optional[List[str]] = None):
        settings = get_settings()
        self.channels = channels if channels is not None else settings.force_sub_channels

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Skip if no channels configured
        if not self.channels:
            return await handler(event, data)

        user = None
        if isinstance(event, Message) and event.from_user:
            user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user

        if user is None:
            return await handler(event, data)

        # Admins always pass
        settings = get_settings()
        if user.id in settings.admin_ids:
            return await handler(event, data)

        bot: Bot = data["bot"]

        not_subscribed = []
        for channel in self.channels:
            try:
                member = await bot.get_chat_member(chat_id=channel, user_id=user.id)
                if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                    not_subscribed.append(channel)
            except Exception as e:
                logger.warning("Cannot check subscription for %s: %s", channel, e)

        if not not_subscribed:
            return await handler(event, data)

        # Build "Subscribe" keyboard
        buttons = []
        for ch in not_subscribed:
            if ch.startswith("@"):
                url = f"https://t.me/{ch.lstrip('@')}"
            else:
                url = f"https://t.me/c/{str(ch).replace('-100', '')}"
            buttons.append([InlineKeyboardButton(text=f"📢 {ch}", url=url)])

        buttons.append([
            InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        text = (
            "❗️ Botdan foydalanish uchun quyidagi kanallarga obuna bo‘ling:\n\n"
            "Obuna bo‘lgach «✅ Tekshirish» tugmasini bosing."
        )

        try:
            if isinstance(event, Message):
                await event.answer(text, reply_markup=keyboard)
            elif isinstance(event, CallbackQuery):
                await event.message.answer(text, reply_markup=keyboard)
                await event.answer()
        except Exception:
            pass

        return None  # block handler