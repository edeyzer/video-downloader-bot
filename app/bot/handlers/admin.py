"""
Admin panel handlers.
Commands: /stats, /broadcast, /ban, /unban, /grant_premium, /users
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.repositories import UserRepository, DownloadRepository, SubscriptionRepository

logger = logging.getLogger(__name__)
router = Router(name="admin")


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admin_ids


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession, user_repo: UserRepository, download_repo: DownloadRepository):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    total_users = await user_repo.count_users()
    premium_users = await user_repo.count_premium_users()
    stats_7d = await download_repo.get_stats(days=7)
    stats_1d = await download_repo.get_stats(days=1)
    blocked = (await session.execute(select(func.count(User.id)).where(User.is_blocked.is_(True)))).scalar_one()

    text = (
        f"📊 <b>Bot Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"⭐ Premium: <b>{premium_users}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>\n\n"
        f"<b>Oxirgi 24 soat:</b>\n"
        f"• Yuklashlar: {stats_1d['total']}\n"
        f"• Muvaffaqiyatli: {stats_1d['success']}\n"
        f"• Cache hit: {stats_1d['cached']} ({stats_1d['cache_hit_rate']}%)\n"
        f"• Xatolik: {stats_1d['failed']}\n\n"
        f"<b>Oxirgi 7 kun:</b>\n"
        f"• Yuklashlar: {stats_7d['total']}\n"
        f"• Muvaffaqiyatli: {stats_7d['success']}\n"
        f"• Cache hit rate: {stats_7d['cache_hit_rate']}%\n"
        f"• Xatolik: {stats_7d['failed']}"
    )
    await message.answer(text)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot, session: AsyncSession, command: CommandObject):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("Ishlatish:\n<code>/broadcast Xabar matni</code>")
        return

    result = await session.execute(select(User.id).where(User.is_blocked.is_(False)))
    user_ids = [row[0] for row in result.all()]
    status = await message.answer(f"📢 Broadcast boshlandi... (0/{len(user_ids)})")
    sent = failed = 0

    for idx, uid in enumerate(user_ids, 1):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except (TelegramAPIError, Exception):
            failed += 1
        if idx % 25 == 0:
            try:
                await status.edit_text(f"📢 Broadcast... ({idx}/{len(user_ids)})\n✅ {sent} | ❌ {failed}")
            except Exception:
                pass
            await asyncio.sleep(0.05)

    await status.edit_text(f"✅ Broadcast yakunlandi\nJami: {len(user_ids)}\nYuborildi: <b>{sent}</b>\nXato: <b>{failed}</b>")


@router.message(Command("ban"))
async def cmd_ban(message: Message, session: AsyncSession, user_repo: UserRepository, command: CommandObject):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip().split()
    if not args:
        await message.answer("Ishlatish: <code>/ban user_id</code>")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await message.answer("user_id son bo‘lishi kerak.")
        return
    user = await user_repo.set_blocked(target_id, blocked=True)
    await session.commit()
    await message.answer(f"🚫 User <code>{target_id}</code> {'bloklandi' if user else 'topilmadi'}.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, session: AsyncSession, user_repo: UserRepository, command: CommandObject):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip().split()
    if not args:
        await message.answer("Ishlatish: <code>/unban user_id</code>")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await message.answer("user_id son bo‘lishi kerak.")
        return
    user = await user_repo.set_blocked(target_id, blocked=False)
    await session.commit()
    await message.answer(f"✅ User <code>{target_id}</code> {'blokdan chiqarildi' if user else 'topilmadi'}.")


@router.message(Command("grant_premium"))
async def cmd_grant_premium(message: Message, session: AsyncSession, user_repo: UserRepository, sub_repo: SubscriptionRepository, command: CommandObject):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip().split()
    if not args:
        await message.answer("Ishlatish: <code>/grant_premium user_id [days]</code>")
        return
    try:
        target_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else 0
    except ValueError:
        await message.answer("user_id va days son bo‘lishi kerak.")
        return

    expires = None
    if days > 0:
        expires = datetime.now(timezone.utc) + timedelta(days=days)

    await user_repo.update_premium_status(target_id, is_premium=True, premium_until=expires)
    await sub_repo.deactivate_all_for_user(target_id)
    await sub_repo.create(user_id=target_id, plan="lifetime" if days == 0 else f"{days}d", expires_at=expires, payment_provider="manual_admin", is_active=True)
    await session.commit()
    until_text = "cheksiz" if not expires else expires.strftime("%Y-%m-%d")
    await message.answer(f"⭐ User <code>{target_id}</code> ga Premium berildi ({until_text})")


@router.message(Command("users"))
async def cmd_users(message: Message, session: AsyncSession, command: CommandObject):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Ishlatish: <code>/users user_id</code> yoki <code>/users @username</code>")
        return
    if arg.startswith("@"):
        result = await session.execute(select(User).where(User.username == arg.lstrip("@")).limit(1))
    else:
        try:
            uid = int(arg)
        except ValueError:
            await message.answer("Noto‘g‘ri format.")
            return
        result = await session.execute(select(User).where(User.id == uid).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer("Topilmadi.")
        return
    text = (
        f"👤 <b>User info</b>\n\n"
        f"ID: <code>{user.id}</code>\nUsername: @{user.username or '—'}\nName: {user.full_name or '—'}\n"
        f"Premium: {'✅' if user.is_premium else '❌'}\nBlocked: {'🚫' if user.is_blocked else '—'}\n"
        f"Daily downloads: {user.daily_downloads}\nPremium until: {user.premium_until or '—'}\nJoined: {user.created_at}"
    )
    await message.answer(text)