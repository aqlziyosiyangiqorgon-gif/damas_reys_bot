from datetime import datetime
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from config import settings
from database.db import Database
from handlers.admin import admin_router
from handlers.common import common_router
from handlers.driver import driver_router
from handlers.parent import parent_router
from handlers.staff import staff_router
from middlewares.roles import (
    InvalidCallbackMiddleware,
    RoleMiddleware,
    TelegramApiFailureMiddleware,
)
from utils.clock import now_tz


@pytest.mark.asyncio
async def test_parent_schedule_reply_button_reaches_parent_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", "6273865667")
    parent_tg_id = 998877661
    db = Database(str(tmp_path / "parent_menu.db"))
    await db.init_db()

    parent_id = await db.add_parent(parent_tg_id, "+998901234567")
    student_id = await db.add_student(
        "Sinov O'quvchisi",
        village_id=1,
        parent_phone_1="+998901234567",
    )
    await db.link_student_parent(student_id, parent_id)
    today = now_tz()
    await db.add_schedule(student_id, weekday=today.weekday(), start_time="09:00", end_time="11:00")

    dp = Dispatcher()
    dp["db"] = db
    dp.message.middleware(RoleMiddleware(db))
    dp.callback_query.middleware(RoleMiddleware(db))
    dp.message.middleware(TelegramApiFailureMiddleware())
    dp.callback_query.middleware(TelegramApiFailureMiddleware())
    dp.callback_query.middleware(InvalidCallbackMiddleware())
    for router in (common_router, admin_router, driver_router, staff_router, parent_router):
        router_copy = deepcopy(router)
        router_copy._parent_router = None
        dp.include_router(router_copy)

    bot = Bot(token="123456:TESTTOKEN")
    bot.session.make_request = AsyncMock(return_value=Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=parent_tg_id, type="private"),
        text="ok",
    ))
    update = Update(
        update_id=1,
        message=Message(
            message_id=2,
            date=datetime.now(),
            chat=Chat(id=parent_tg_id, type="private"),
            from_user=User(id=parent_tg_id, is_bot=False, first_name="Parent"),
            text="📅 Dars jadvali",
        ),
    )

    try:
        await dp.feed_update(bot, update, db=db)
        sent_messages = [
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)
        ]
        assert sent_messages
        assert "Sinov O'quvchisi" in sent_messages[0].text
        assert "Dars vaqtlari" in sent_messages[0].text
        assert "09:00 - 11:00" in sent_messages[0].text
    finally:
        await bot.session.close()
