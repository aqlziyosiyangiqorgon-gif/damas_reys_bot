"""Helpers for the optional image shown when a user starts the bot."""

from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile

from utils.telegram import safe_telegram_call


BANNER_PATH = Path(__file__).resolve().parent.parent / "assets" / "banner.jpg"


async def answer_start(message: Any, text: str, *, reply_markup: Any = None) -> None:
    """Send the welcome as a photo when available, otherwise send plain text."""
    if BANNER_PATH.is_file():
        sent = await safe_telegram_call(
            message.answer_photo,
            photo=FSInputFile(BANNER_PATH),
            caption=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        if sent:
            return

    await safe_telegram_call(
        message.answer,
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )
