from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from database.db import Database
from config import settings
from models.enums import UserRole
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery
import logging

logger = logging.getLogger(__name__)

class RoleMiddleware(BaseMiddleware):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user: User = data.get("event_from_user")
        if user:
            role: UserRole = await self.db.get_user_role(user.id, settings.admin_ids_list)
            data["user_role"] = role
        else:
            data["user_role"] = UserRole.GUEST

        return await handler(event, data)


class TelegramApiFailureMiddleware(BaseMiddleware):
    """Keep a rejected/transient Telegram API call from terminating update polling."""

    async def __call__(self, handler, event: TelegramObject, data: Dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest,
                TelegramNetworkError, TelegramServerError) as exc:
            # Never log the exception message: API errors can include request URL details.
            logger.warning("Telegram API call failed while handling update (%s)", type(exc).__name__)
            return None


class InvalidCallbackMiddleware(BaseMiddleware):
    """Turn malformed numeric callback payloads into a user-facing rejection."""

    async def __call__(self, handler, event: TelegramObject, data: Dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except (ValueError, IndexError) as exc:
            if not isinstance(event, CallbackQuery):
                raise
            logger.info("Rejected malformed callback payload (%s)", type(exc).__name__)
            await event.answer("Tugma ma'lumoti eskirgan yoki noto'g'ri. Menyuni qayta oching.", show_alert=True)
            return None
