from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from database.db import Database
from config import settings
from models.enums import UserRole

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
