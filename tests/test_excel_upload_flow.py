from copy import deepcopy
from datetime import datetime
from shutil import copyfile
from unittest.mock import AsyncMock

import openpyxl
import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Document, Message, Update, User

from config import settings
from database.db import Database
from handlers.admin import (
    ExcelSaveCallback,
    admin_router,
    get_admin_menu_kb,
    PENDING_EXCEL_IMPORTS,
)
from handlers.common import common_router
from handlers.driver import driver_router
from handlers.parent import parent_router
from handlers.staff import staff_router
from middlewares.roles import (
    InvalidCallbackMiddleware,
    RoleMiddleware,
    TelegramApiFailureMiddleware,
)


@pytest.mark.asyncio
async def test_admin_excel_upload_confirm_inserts_new_student(tmp_path, monkeypatch):
    configured_admin_id = settings.admin_ids_list[0] if settings.admin_ids_list else 6273865667
    admin_id = configured_admin_id
    monkeypatch.setattr(settings, "ADMIN_IDS", str(admin_id))

    db = Database(str(tmp_path / "excel_flow.db"))
    await db.init_db()
    initial_students = await db.get_active_students()
    village = next(v for v in await db.get_villages() if v["name"] == "Arpaariq")

    excel_path = tmp_path / "one_student.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([
        "F.I.Sh", "Mahalla", "Ota telefoni", "Ona telefoni",
        "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba",
    ])
    sheet.append([
        "Excel Sinov O'quvchisi", village["name"], "+998901234567", "",
        "09:00-11:00", "", "", "", "", "",
    ])
    workbook.save(excel_path)

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

    async def fake_make_request(_bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(
            message_id=100,
            date=datetime.now(),
            chat=Chat(id=admin_id, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Test bot"),
            text=getattr(method, "text", "ok"),
        )

    async def fake_download(_file, destination, **_kwargs):
        copyfile(excel_path, destination)
        return destination

    bot.session.make_request = AsyncMock(side_effect=fake_make_request)
    bot.download = AsyncMock(side_effect=fake_download)

    upload = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=admin_id, type="private"),
            from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
            document=Document(
                file_id="test-file-id",
                file_unique_id="test-file-unique-id",
                file_name="one_student.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                file_size=excel_path.stat().st_size,
            ),
        ),
    )

    try:
        await dp.feed_update(bot, upload, db=db)
        preview_request = next(
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)
            and "Fayl tahlili" in call.args[1].text
        )
        assert "Yangi o'quvchilar: <b>1 ta</b>" in preview_request.text
        save_data = preview_request.reply_markup.inline_keyboard[0][0].callback_data
        callback_data = ExcelSaveCallback.unpack(save_data)

        preview_message = Message(
            message_id=100,
            date=datetime.now(),
            chat=Chat(id=admin_id, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Test bot"),
            text=preview_request.text,
            reply_markup=preview_request.reply_markup,
        )
        callback = Update(
            update_id=2,
            callback_query=CallbackQuery(
                id="test-callback-id",
                from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                chat_instance="test-chat-instance",
                message=preview_message,
                data=callback_data.pack(),
            ),
        )
        await dp.feed_update(bot, callback, db=db)

        saved_students = await db.get_active_students()
        assert len(saved_students) == len(initial_students) + 1
        assert any(student["full_name"] == "Excel Sinov O'quvchisi" for student in saved_students)

        menu_rows = get_admin_menu_kb().keyboard
        assert [button.text for button in menu_rows[0]] == [
            "📤 O'quvchilar ro'yxatini Excel",
            "📥 Excel boshqaruvi",
        ]
        export_update = Update(
            update_id=3,
            message=Message(
                message_id=3,
                date=datetime.now(),
                chat=Chat(id=admin_id, type="private"),
                from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
                text="📤 O'quvchilar ro'yxatini Excel",
            ),
        )
        await dp.feed_update(bot, export_update, db=db)
        export_requests = [
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendDocument)
        ]
        assert len(export_requests) == 1
        assert export_requests[0].document.filename == "baza_oquvchilar.xlsx"
    finally:
        PENDING_EXCEL_IMPORTS.clear()
        await bot.session.close()
