from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, PhotoSize, Update, User

from database.db import Database
from handlers.admin import admin_router
from handlers.common import common_router
from handlers.driver import driver_router
from handlers.parent import ParentStates, parent_router, reject_non_text_absence_reason
from handlers.staff import staff_router
from middlewares.roles import (
    InvalidCallbackMiddleware,
    RoleMiddleware,
    TelegramApiFailureMiddleware,
)

EXPECTED_REPLY = "Iltimos, sababni faqat matn ko'rinishida yozib yuboring."


@pytest.mark.asyncio
async def test_non_text_absence_reason_handler_replies_exact_text():
    message = MagicMock(spec=Message)
    message.from_user = User(id=12345, is_bot=False, first_name="Parent")
    message.text = None
    message.answer = AsyncMock()

    await reject_non_text_absence_reason(message)

    message.answer.assert_awaited_once_with(EXPECTED_REPLY)


@pytest.mark.asyncio
async def test_parent_photo_in_absence_reason_state_is_answered_through_dispatcher(tmp_path):
    parent_tg_id = 998877662
    db = Database(str(tmp_path / "parent_photo.db"))
    await db.init_db()
    await db.add_parent(parent_tg_id, "+998901234567")

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

    key = StorageKey(bot_id=bot.id, chat_id=parent_tg_id, user_id=parent_tg_id)
    await dp.storage.set_state(key, ParentStates.waiting_for_absence_reason)

    update = Update(
        update_id=1,
        message=Message(
            message_id=2,
            date=datetime.now(),
            chat=Chat(id=parent_tg_id, type="private"),
            from_user=User(id=parent_tg_id, is_bot=False, first_name="Parent"),
            photo=[PhotoSize(file_id="photo-id", file_unique_id="photo-uid", width=10, height=10)],
        ),
    )

    try:
        await dp.feed_update(bot, update, db=db)
        sent = [
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)
        ]
        assert [method.text for method in sent] == [EXPECTED_REPLY]
        # Holat saqlanib qoladi: ota-ona keyingi xabarida matn yuborib davom ettira oladi.
        assert await dp.storage.get_state(key) == ParentStates.waiting_for_absence_reason.state
    finally:
        await bot.session.close()
