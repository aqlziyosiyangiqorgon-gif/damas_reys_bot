import asyncio

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage
from aiogram.exceptions import TelegramNetworkError
import openpyxl
from unittest.mock import AsyncMock

from database.db import Database
from middlewares.sqlite_storage import SQLiteFSMStorage
from utils.text import escape_html
from utils.time import now_tashkent
from utils.telegram import safe_telegram_call
from services.excel_service import ExcelService


@pytest.mark.asyncio
async def test_atomic_trip_and_item_claims(tmp_path):
    db = Database(str(tmp_path / "hardening.db"))
    await db.init_db()
    driver_id = await db.add_driver(10001, "Driver", "+998901234567")
    student_id = await db.add_student("Student", 1, "+998901234567")

    conn = await db.get_connection()
    cur = await conn.execute(
        "INSERT INTO trips (date, direction, driver_id, planned_time, status, approved) "
        "VALUES ('2026-10-14', 'to_center', ?, '08:00', 'approved', 1)",
        (driver_id,),
    )
    trip_id = cur.lastrowid
    cur_item = await conn.execute(
        "INSERT INTO trip_items (trip_id, student_id, village_id, status) VALUES (?, ?, 1, 'pending')",
        (trip_id, student_id),
    )
    item_id = cur_item.lastrowid
    await conn.commit()
    await conn.close()

    transitions = await asyncio.gather(*[
        db.transition_trip(
            trip_id, ["approved"], "on_way", started_at="2026-10-14 08:00:00",
            direction="to_center", actor_tg_id=10001, event_action="start_trip",
        )
        for _ in range(2)
    ])
    assert sorted(transitions) == [False, True]

    claims = await asyncio.gather(*[
        db.claim_item(
            item_id, trip_id, ["pending"], "picked_up", "picked_at", "2026-10-14 08:01:00",
            driver_id, trip_statuses=["on_way"], direction="to_center",
            actor_tg_id=10001, event_action="pickup_student",
        )
        for _ in range(2)
    ])
    assert sorted(claims) == [False, True]

    conn = await db.get_connection()
    cur = await conn.execute("SELECT COUNT(*) FROM event_log WHERE trip_id = ?", (trip_id,))
    assert (await cur.fetchone())[0] == 2
    await conn.close()


@pytest.mark.asyncio
async def test_fsm_storage_and_admin_workflows_persist(tmp_path):
    db = Database(str(tmp_path / "storage.db"))
    await db.init_db()
    key = StorageKey(bot_id=7, chat_id=8, user_id=9, destiny="default")

    first = SQLiteFSMStorage(db)
    await first.set_state(key, "AdminStates:waiting")
    await first.set_data(key, {"student_id": 42})
    second = SQLiteFSMStorage(db)
    assert await second.get_state(key) == "AdminStates:waiting"
    assert await second.update_data(key, {"name": "Ali"}) == {"student_id": 42, "name": "Ali"}
    assert await first.get_data(key) == {"student_id": 42, "name": "Ali"}

    payload = {"records": [{"full_name": "Ali", "village_id": 1}]}
    await db.save_workflow_payload("batch-1", 99, "excel", payload)
    assert await db.take_workflow_payload("batch-1", 100, "excel") is None
    assert await db.take_workflow_payload("batch-1", 99, "excel") == payload
    assert await db.take_workflow_payload("batch-1", 99, "excel") is None


def test_html_and_application_timezone_helpers():
    assert escape_html('<name "x">') == '&lt;name "x"&gt;'
    assert now_tashkent().utcoffset().total_seconds() == 5 * 60 * 60


@pytest.mark.asyncio
async def test_telegram_retry_handles_transient_errors():
    method = AsyncMock(side_effect=[TelegramNetworkError(SendMessage(chat_id=1, text="x"), "offline"), None])
    assert await safe_telegram_call(method, retries=2)
    assert method.await_count == 2


@pytest.mark.asyncio
async def test_excel_time_validation_and_formula_safe_export(tmp_path):
    db = Database(str(tmp_path / "excel.db"))
    await db.init_db()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["F.I.Sh", "Mahalla", "Ota telefoni", "Ona telefoni", "Dushanba"])
    sheet.append(["Student", "Arpaariq", "+998901234567", "", "10:00-09:00"])
    source = tmp_path / "invalid.xlsx"
    workbook.save(source)
    workbook.close()

    preview = await ExcelService.parse_and_preview_excel(str(source), db)
    assert preview["valid_records"] == []
    assert preview["error_count"] == 1
    assert "tugash vaqti" in preview["errors"][0]["error"]

    await db.add_student("=1+1", 1, "+998901234567")
    exported = await ExcelService.export_students_to_excel(db, str(tmp_path / "export.xlsx"))
    exported_workbook = openpyxl.load_workbook(exported, data_only=False)
    assert exported_workbook.active.cell(row=2, column=1).value == "'=1+1"
    assert exported_workbook.active.cell(row=2, column=1).data_type == "s"
    exported_workbook.close()
