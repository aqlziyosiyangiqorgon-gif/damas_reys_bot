import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database.db import Database
from handlers.admin import _generate_today_plan
from scripts.migrate_schema import migrate
from utils.time_validation import validate_schedule_times


@pytest.mark.asyncio
async def test_migration_backs_up_and_adds_unique_and_driver_foreign_keys(tmp_path):
    db_path = tmp_path / "transport.db"
    backup_dir = tmp_path / "backups"
    db = Database(str(db_path))
    await db.init_db()
    driver_id = await db.add_driver(telegram_id=101, full_name="Haydovchi", phone="+998901234567")
    village_id = next(v["id"] for v in await db.get_villages() if v["name"] == "Galaravat")
    student_id = await db.add_student("Bola", village_id, "+998901234567")
    conn = await db.get_connection()
    try:
        await conn.execute("CREATE TABLE protected_groups (id INTEGER PRIMARY KEY, chat_id INTEGER)")
        await conn.execute("CREATE TABLE required_channels (id INTEGER PRIMARY KEY, chat_id INTEGER)")
        await conn.commit()
    finally:
        await conn.close()

    backup = migrate(str(db_path), str(backup_dir))
    assert sqlite3.connect(backup).execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1

    raw = sqlite3.connect(db_path)
    try:
        for table, column in (("trips", "driver_id"), ("villages", "default_driver_id")):
            assert any(row[2] == "drivers" and row[3] == column for row in raw.execute(f'PRAGMA foreign_key_list("{table}")'))
        assert raw.execute("SELECT 1 FROM sqlite_master WHERE name='uq_trip_items_trip_student'").fetchone()
        assert raw.execute("SELECT 1 FROM sqlite_master WHERE name='uq_protected_groups_chat_id'").fetchone()
        assert raw.execute("SELECT 1 FROM sqlite_master WHERE name='uq_required_channels_chat_id'").fetchone()
        raw.execute("INSERT INTO trips(date,direction,driver_id,planned_time) VALUES('2026-10-14','to_center',?,'09:00')", (driver_id,))
        trip_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
        raw.execute("INSERT INTO trip_items(trip_id,student_id,village_id) VALUES(?,?,?)", (trip_id, student_id, village_id))
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("INSERT INTO trip_items(trip_id,student_id,village_id) VALUES(?,?,?)", (trip_id, student_id, village_id))
        raw.commit()
    finally:
        raw.close()


@pytest.mark.asyncio
async def test_duplicate_generated_item_fails_gracefully_and_rolls_back(tmp_path):
    db = Database(str(tmp_path / "db.sqlite"))
    await db.init_db()
    migrate(str(tmp_path / "db.sqlite"), str(tmp_path / "backups"))
    village_id = next(v["id"] for v in await db.get_villages() if v["name"] == "Galaravat")
    student_id = await db.add_student("Test Bola", village_id, "+998901234567")
    assert await db.save_generated_trips([
        {"direction": "to_center", "planned_time": "09:00", "items": [
            {"student_id": student_id, "village_id": village_id},
            {"student_id": student_id, "village_id": village_id},
        ]}
    ], "2026-10-14") is False
    assert await db.get_unapproved_trips("2026-10-14") == []


@pytest.mark.asyncio
async def test_duplicate_integrity_failure_is_shown_as_user_message():
    message = SimpleNamespace(answer=AsyncMock())
    db = SimpleNamespace(get_day_schedules=AsyncMock(return_value=[]))
    scheduler = SimpleNamespace(run_daily_plan=AsyncMock(return_value=(False, "save_failed", [])))
    await _generate_today_plan(message, db, scheduler)
    assert "takroriy o'quvchi" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_database_rejects_unpadded_or_non_increasing_schedule_times(tmp_path):
    db = Database(str(tmp_path / "schedule.db"))
    await db.init_db()
    village_id = next(v["id"] for v in await db.get_villages() if v["name"] == "Galaravat")
    student_id = await db.add_student("Student", village_id, "+998901234567")
    for start, end in (("9:00", "10:00"), ("11:00", "10:00")):
        with pytest.raises(ValueError):
            await db.add_schedule(student_id, 0, start, end)


@pytest.mark.parametrize("start,end", [("9:00", "10:00"), ("09:00", "09:00"), ("10:00", "09:00"), ("24:00", "25:00")])
def test_schedule_time_validation_rejects_invalid_interval(start, end):
    with pytest.raises(ValueError):
        validate_schedule_times(start, end)


def test_schedule_time_validation_accepts_padded_increasing_interval():
    validate_schedule_times("09:00", "10:00")
