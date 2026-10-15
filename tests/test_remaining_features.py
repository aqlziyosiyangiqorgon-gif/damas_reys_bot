import pytest
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database.db import Database
from models.enums import TripStatus
from handlers import admin as admin_handlers
from models.callbacks import CancelTripCallback
from services.notify_service import NotifyService
from services.excel_service import ExcelService
from handlers.staff import get_staff_menu_kb
from utils.clock import now_tz


@pytest.mark.asyncio
async def test_approve_trip_only_changes_the_selected_trip(tmp_path):
    db = Database(str(tmp_path / "approve.db"))
    await db.init_db()
    conn = await db.get_connection()
    try:
        await conn.executemany(
            "INSERT INTO trips(date,direction,planned_time,status,approved) "
            "VALUES('2026-10-15','to_center','09:00','planned',0)",
            [(), ()],
        )
        await conn.commit()
        cur = await conn.execute("SELECT id FROM trips ORDER BY id")
        ids = [row[0] for row in await cur.fetchall()]
    finally:
        await conn.close()

    assert await db.approve_trip(ids[0], admin_id=42)
    trips = await db.get_trips_by_date("2026-10-15")
    by_id = {trip["id"]: trip for trip in trips}
    assert by_id[ids[0]]["status"] == TripStatus.APPROVED.value
    assert by_id[ids[0]]["approved"] == 1
    assert by_id[ids[1]]["status"] == TripStatus.PLANNED.value
    assert by_id[ids[1]]["approved"] == 0


def test_default_excel_template_name_is_unique_and_outside_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("services.excel_service.tempfile.gettempdir", lambda: str(tmp_path))
    first = ExcelService.generate_template()
    second = ExcelService.generate_template()
    try:
        assert first != second
        assert os.path.dirname(first) == str(tmp_path)
        assert os.path.basename(first).startswith("shablon_oquvchilar_")
        assert os.path.exists(first) and os.path.exists(second)
    finally:
        for path in (first, second):
            if os.path.exists(path):
                os.remove(path)


def test_staff_menu_contains_topshirish_command():
    buttons = [button.text for row in get_staff_menu_kb().keyboard for button in row]
    assert "/topshirish" in buttons


@pytest.mark.asyncio
async def test_trip_cancellation_sets_cancelled_and_rejects_on_way_or_done(tmp_path):
    db = Database(str(tmp_path / "cancel.db"))
    await db.init_db()
    conn = await db.get_connection()
    try:
        for status in ("planned", "on_way", "done"):
            await conn.execute(
                "INSERT INTO trips(date,direction,planned_time,status,approved) VALUES(?,?,?,?,0)",
                ("2026-10-15", "to_center", "09:00", status),
            )
        await conn.commit()
        cur = await conn.execute("SELECT id,status FROM trips ORDER BY id")
        planned_id, on_way_id, done_id = [row[0] for row in await cur.fetchall()]
    finally:
        await conn.close()

    assert await db.cancel_trip(planned_id, admin_id=42)
    assert not await db.cancel_trip(on_way_id, admin_id=42)
    assert not await db.cancel_trip(done_id, admin_id=42)
    trip = await db.get_trip_details(planned_id)
    assert trip["status"] == TripStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_trip_cancel_handler_notifies_parents(monkeypatch):
    monkeypatch.setattr(admin_handlers, "is_admin", lambda _user_id: True)
    notify = AsyncMock()
    monkeypatch.setattr(NotifyService, "notify_trip_students", notify)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data=CancelTripCallback(trip_id=7).pack(),
        bot=object(),
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    db = SimpleNamespace(cancel_trip=AsyncMock(return_value=True))
    await admin_handlers.cb_admin_cancel_trip(callback, db)
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["trip_id"] == 7
    assert "bekor qilindi" in notify.await_args.kwargs["text_template"]


@pytest.mark.asyncio
async def test_deactivate_student_removes_only_todays_planned_trip_item(tmp_path):
    db = Database(str(tmp_path / "deactivate.db"))
    await db.init_db()
    village_id = next(v["id"] for v in await db.get_villages() if v["name"] == "Galaravat")
    student_id = await db.add_student("Bola", village_id, "+998901234567")
    today = now_tz().date()
    conn = await db.get_connection()
    trip_ids = {}
    try:
        for key, trip_date, status, approved in (
            ("today_planned", today.isoformat(), "planned", 0),
            ("today_approved", today.isoformat(), "approved", 1),
            ("old_planned", (today - timedelta(days=1)).isoformat(), "planned", 0),
        ):
            cur = await conn.execute(
                "INSERT INTO trips(date,direction,planned_time,status,approved) VALUES(?,?,?,?,?)",
                (trip_date, "to_center", "09:00", status, approved),
            )
            trip_ids[key] = cur.lastrowid
            await conn.execute(
                "INSERT INTO trip_items(trip_id,student_id,village_id,status) VALUES(?,?,?,'pending')",
                (cur.lastrowid, student_id, village_id),
            )
        await conn.commit()
    finally:
        await conn.close()

    assert await db.deactivate_student(student_id)
    remaining = {}
    for key, trip_id in trip_ids.items():
        trip = await db.get_trip_details(trip_id)
        remaining[key] = len(trip["items"])
    student = await db.get_student_by_id(student_id)
    assert remaining == {"today_planned": 0, "today_approved": 1, "old_planned": 1}
    assert student["is_active"] == 0
