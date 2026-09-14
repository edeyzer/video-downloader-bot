"""
Telegram Stars (XTR) payment service.
Creates invoices, validates pre-checkout and activates Premium on successful payment.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories import UserRepository, SubscriptionRepository

logger = logging.getLogger(__name__)


# ======================
# Plan definitions (Telegram Stars)
# ======================
PLANS = {
    "monthly": {
        "title": "Premium 1 oy",
        "description": "1 oy cheksiz yuklash, 1080p, video qirqish (trim)",
        "payload": "premium_monthly",
        "currency": "XTR",
        "amount": 50,          # 50 Stars
        "days": 30,
    },
    "quarterly": {
        "title": "Premium 3 oy",
        "description": "3 oy cheksiz yuklash, 1080p, video qirqish",
        "payload": "premium_quarterly",
        "currency": "XTR",
        "amount": 120,
        "days": 90,
    },
    "yearly": {
        "title": "Premium 1 yil",
        "description": "1 yil cheksiz yuklash, 1080p, video qirqish",
        "payload": "premium_yearly",
        "currency": "XTR",
        "amount": 400,
        "days": 365,
    },
    "lifetime": {
        "title": "Premium Lifetime",
        "description": "Umrbod cheksiz yuklash va barcha imkoniyatlar",
        "payload": "premium_lifetime",
        "currency": "XTR",
        "amount": 800,
        "days": None,          # lifetime
    },
}


class PaymentService:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = get_settings()

    async def send_invoice(self, chat_id: int, plan_key: str) -> Optional[Message]:
        """
        Send Telegram Stars invoice for the given plan.
        provider_token must be empty string for digital goods / Stars.
        """
        plan = PLANS.get(plan_key)
        if not plan:
            logger.warning("Unknown plan_key: %s", plan_key)
            return None

        prices = [LabeledPrice(label=plan["title"], amount=plan["amount"])]

        return await self.bot.send_invoice(
            chat_id=chat_id,
            title=plan["title"],
            description=plan["description"],
            payload=plan["payload"],
            provider_token="",               # empty = Telegram Stars
            currency=plan["currency"],      # XTR
            prices=prices,
            start_parameter=f"premium_{plan_key}",
        )

    async def process_pre_checkout(self, query: PreCheckoutQuery) -> bool:
        """
        Validate the invoice payload and answer the pre-checkout query.
        """
        valid_payloads = {p["payload"] for p in PLANS.values()}

        if query.invoice_payload not in valid_payloads:
            await query.answer(ok=False, error_message="Noto‘g‘ri to‘lov ma’lumoti.")
            return False

        await query.answer(ok=True)
        return True

    async def process_successful_payment(
        self,
        message: Message,
        session: AsyncSession,
        user_repo: UserRepository,
        sub_repo: SubscriptionRepository,
    ) -> bool:
        """
        Handle successful_payment update:
        - find plan by payload
        - update User.is_premium + premium_until
        - create Subscription record
        - send confirmation message
        """
        payment = message.successful_payment
        if not payment:
            return False

        user = message.from_user
        if not user:
            return False

        # Find plan by payload
        plan_key = None
        for key, plan in PLANS.items():
            if plan["payload"] == payment.invoice_payload:
                plan_key = key
                break

        if not plan_key:
            logger.error("Unknown payment payload: %s", payment.invoice_payload)
            await message.answer("❌ To‘lov qabul qilindi, lekin tarif aniqlanmadi. Admin bilan bog‘laning.")
            return False

        plan = PLANS[plan_key]
        days = plan["days"]
        expires: Optional[datetime] = None
        if days is not None:
            expires = datetime.now(timezone.utc) + timedelta(days=days)

        # Ensure user exists in DB
        await user_repo.get_or_create(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            language_code=user.language_code,
        )

        # Activate premium
        await user_repo.update_premium_status(
            user_id=user.id,
            is_premium=True,
            premium_until=expires,
        )

        # Deactivate old subscriptions and create new one
        await sub_repo.deactivate_all_for_user(user.id)
        await sub_repo.create(
            user_id=user.id,
            plan=plan_key,
            amount=payment.total_amount,
            currency=payment.currency,
            expires_at=expires,
            payment_provider="telegram_stars",
            payment_id=payment.telegram_payment_charge_id,
            is_active=True,
        )
        await session.commit()

        until_text = "cheksiz" if expires is None else expires.strftime("%Y-%m-%d %H:%M UTC")
        await message.answer(
            f"🎉 <b>To‘lov muvaffaqiyatli amalga oshirildi!</b>\n\n"
            f"⭐ Premium faollashtirildi.\n"
            f"Tarif: <b>{plan['title']}</b>\n"
            f"Amal qilish muddati: <b>{until_text}</b>\n\n"
            f"Endi sizda:\n"
            f"• Cheksiz yuklash\n"
            f"• 1080p / yuqori sifat\n"
            f"• Video qirqish (trim)\n"
            f"• Navbatsiz yuklash\n\n"
            f"Rahmat! 🚀"
        )

        logger.info(
            "Premium activated via Stars | user=%s plan=%s charge_id=%s amount=%s %s",
            user.id,
            plan_key,
            payment.telegram_payment_charge_id,
            payment.total_amount,
            payment.currency,
        )
        return True