"""
/start command handler.
Creates or updates user in database and shows welcome message.
"""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import UserRepository
from app.config import get_settings

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    user_repo: UserRepository,
):
    """Handle /start command."""
    user = message.from_user
    if not user:
        return

    db_user, created = await user_repo.get_or_create(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
    )

    settings = get_settings()

    if created:
        text = (
            f"👋 Salom, <b>{user.full_name or user.username or 'do‘st'}</b>!\n\n"
            f"Men professional Video Downloader botman.\n"
            f"YouTube, Instagram, TikTok, Pinterest va boshqa platformalardan "
            f"video / audio yuklab olishim mumkin.\n\n"
            f"🔗 Shunchaki linkni yuboring — men qolganini qilaman.\n\n"
            f"📊 Kunlik limit (bepul): <b>{settings.free_daily_limit}</b> ta\n"
            f"🎬 Maksimal sifat (bepul): <b>{settings.free_max_quality}p</b>\n\n"
            f"Premium haqida bilish uchun /premium buyrug‘ini yuboring."
        )
    else:
        text = (
            f"👋 Qaytganingiz bilan, <b>{user.full_name or user.username or 'do‘st'}</b>!\n\n"
            f"Link yuboring yoki /help buyrug‘ini bosing."
        )

    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Simple help message."""
    text = (
        "📖 <b>Yordam</b>\n\n"
        "1. Istalgan platformadan video/reels/shorts linkini yuboring.\n"
        "2. Bot avtomatik yuklab, sizga yuboradi.\n"
        "3. Bir marta yuklangan video keyingi safar <b>0.1 soniyada</b> qaytariladi (cache).\n\n"
        "<b>Qo‘llab-quvvatlanadigan platformalar:</b>\n"
        "• YouTube (Video, Shorts, MP3)\n"
        "• Instagram (Reels, Post, Carousel)\n"
        "• TikTok (watermark’siz)\n"
        "• Pinterest va boshqalar\n\n"
        "<b>Buyruqlar:</b>\n"
        "/start — Botni qayta ishga tushirish\n"
        "/help — Shu yordam\n"
        "/premium — Premium imkoniyatlari\n"
        "/stats — Shaxsiy statistika (tez orada)"
    )
    await message.answer(text)

@router.message(Command("menu"))
@router.message(Command("services"))
async def cmd_menu(message: Message):
    """Barcha xizmatlar menyusi"""
    text = (
        "📋 <b>Bot xizmatlari</b>\n\n"
        "🔗 <b>Asosiy ishlash:</b>\n"
        "• Istalgan platformadan link yuboring\n"
        "• Video yoki Audio formatini tanlang\n\n"
        "📥 <b>Qo‘llab-quvvatlanadigan platformalar:</b>\n"
        "• YouTube (Video, Shorts)\n"
        "• Instagram (Reels, Post)\n"
        "• TikTok\n"
        "• Pinterest\n"
        "• va yt-dlp qo‘llab-quvvatlaydigan boshqa saytlar\n\n"
        "⚙️ <b>Buyruqlar:</b>\n"
        "/start — Botni qayta ishga tushirish\n"
        "/menu — Shu menyu\n"
        "/mp3 — To‘g‘ridan-to‘g‘ri audio yuklash\n"
        "/premium — Premium obuna\n"
        "/help — Yordam\n\n"
        "💡 <i>Hozircha barcha foydalanuvchilar eng yaxshi sifatda yuklab olishadi.</i>"
    )
    await message.answer(text)

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    """
    Called when user presses «✅ Tekshirish» after force-sub.
    Simply answers and lets the user try again (middleware will re-check).
    """
    await callback.answer(
        "Obuna holati tekshirildi. Endi kerakli amalni qayta bajaring.",
        show_alert=True,
    )
    try:
        await callback.message.delete()
    except Exception:
        pass