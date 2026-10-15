from sqlite3 import IntegrityError
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from config import settings
from database.db import Database
from handlers.admin import process_driver_phone, process_staff_phone, process_student_time


def make_admin_message(text: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.from_user = User(id=6273865667, is_bot=False, first_name="Admin")
    message.text = text
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_duplicate_driver_telegram_id_is_reported_friendly(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", "6273865667")
    message = make_admin_message("+998901234567")
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"tg_id": 111, "full_name": "Sinov Haydovchi"}
    db = AsyncMock(spec=Database)
    db.add_driver.side_effect = IntegrityError("duplicate Telegram id")

    await process_driver_phone(message, state, db)

    message.answer.assert_awaited_once_with("Bu Telegram ID allaqachon ro'yxatda.")
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_staff_telegram_id_is_reported_friendly(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", "6273865667")
    message = make_admin_message("yo'q")
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"tg_id": 222, "full_name": "Sinov Xodim"}
    db = AsyncMock(spec=Database)
    db.add_staff.side_effect = IntegrityError("duplicate Telegram id")

    await process_staff_phone(message, state, db)

    message.answer.assert_awaited_once_with("Bu Telegram ID allaqachon ro'yxatda.")
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_student_is_reported_friendly(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", "6273865667")
    message = make_admin_message("09:00-11:00")
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {
        "full_name": "Sinov O'quvchi",
        "village_id": 1,
        "village_name": "Arpaariq",
        "parent_phone_1": "+998901234567",
        "parent_phone_2": None,
        "selected_days": [0],
        "days_label": "Dushanba",
    }
    db = AsyncMock(spec=Database)
    db.add_student_with_schedules.side_effect = IntegrityError("duplicate student")

    await process_student_time(message, state, db)

    message.answer.assert_awaited_once_with("Bu o'quvchi allaqachon ro'yxatda.")
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_student_with_invalid_schedule_rolls_back_student_and_schedules(tmp_path):
    db = Database(str(tmp_path / "atomic_student_schedule.db"))
    await db.init_db()

    with pytest.raises(ValueError):
        await db.add_student_with_schedules(
            student_fields={
                "full_name": "Sinov O'quvchi",
                "village_id": 1,
                "parent_phone_1": "+998901234567",
            },
            schedules=[{"weekday": 0, "start_time": "11:00", "end_time": "09:00"}],
        )

    conn = await db.get_connection()
    cur_students = await conn.execute("SELECT COUNT(*) FROM students")
    cur_schedules = await conn.execute("SELECT COUNT(*) FROM schedules")
    student_count = (await cur_students.fetchone())[0]
    schedule_count = (await cur_schedules.fetchone())[0]
    await conn.close()

    assert student_count == 0
    assert schedule_count == 0
