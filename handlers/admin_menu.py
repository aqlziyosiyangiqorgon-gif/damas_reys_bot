from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import settings


def get_admin_menu_kb() -> ReplyKeyboardMarkup:
    """Keep only high-frequency admin actions in Telegram; detailed management lives in Mini App."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🚐 Bugungi reyslar"),
        KeyboardButton(text="🚫 Bugun kelmaganlar"),
    )
    builder.row(KeyboardButton(text="💰 Oylik to'lovlar"))
    if settings.MINIAPP_URL:
        builder.row(
            KeyboardButton(
                text="🌐 Admin panel",
                web_app=WebAppInfo(url=settings.MINIAPP_URL),
            )
        )
    return builder.as_markup(resize_keyboard=True, is_persistent=True)
