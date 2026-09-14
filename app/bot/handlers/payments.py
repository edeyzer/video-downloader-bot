"""
Telegram Stars payment handlers (Aiogram 3).
- buy: callback → send_invoice
- pre_checkout_query → validate
- successful_payment → activate premium
"""

import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, PreCheckoutQuery, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import UserRepository, SubscriptionRepository
from app.services.payment_service import PaymentService, PLANS

logger = logging.getLogger(__name__)
router = Router(name="payments")


@router.callback_query(F.data.startswith("buy:"))
async def buy_premium(callback: CallbackQuery, bot: Bot):
    """
    Triggered by inline buttons from /premium (callback_data = buy:monthly, buy:yearly ...).
    Sends a real Telegram Stars invoice.
    """
    await callback.answer()

    if not callback.message:
        return

    plan_key = callback.data.split(":", 1)[1]
    if plan_key not in PLANS:
        await callback.message.answer("❌ Noto‘g‘ri tarif tanlandi.")
        return

    service = PaymentService(bot)
    try:
        await service.send_invoice(
            chat_id=callback.message.chat.id,
            plan_key=plan_key,
        )
    except Exception as e:
        logger.exception("Failed to send Stars invoice")
        await callback.message.answer(
            "❌ To‘lov oynasini ochib bo‘lmadi.\n"
            "Birozdan keyin qayta urinib ko‘ring yoki admin bilan bog‘laning.\n"
            f"<code>{type(e).__name__}: {e}</code>"
        )


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, bot: Bot):
    """
    Telegram sends this just before charging the user.
    We must answer within ~10 seconds.
    """
    service = PaymentService(bot)
    await service.process_pre_checkout(query)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user_repo: UserRepository,
    sub_repo: SubscriptionRepository,
):
    """
    Fired after the user successfully pays with Stars.
    Activates Premium and stores the payment record.
    """
    service = PaymentService(bot)
    await service.process_successful_payment(
        message=message,
        session=session,
        user_repo=user_repo,
        sub_repo=sub_repo,
    )