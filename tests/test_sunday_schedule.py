from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

from database.db import Database
from handlers.admin import process_student_days
from services.excel_service import ExcelService
from services.scheduler_service import SchedulerService


@pytest.mark.asyncio
async def test_every_day_admin_selection_includes_sunday():
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=6273865667),
        data="days_all",
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await process_student_days(callback, state)

    state.update_data.assert_awaited_once_with(
        selected_days=[0, 1, 2, 3, 4, 5, 6],
        days_label="Dushanba - Yakshanba",
    )


@pytest.mark.asyncio
async def test_sunday_schedule_is_used_to_create_sunday_plan(tmp_path):
    db = Database(str(tmp_path / "sunday_schedule.db"))
    await db.init_db()
    student_id = await db.add_student(
        full_name="Yakshanba Test",
        village_id=1,
        parent_phone_1="+998901234567",
    )
    await db.add_schedule(student_id, weekday=6, start_time="09:00", end_time="11:00")

    scheduler = SchedulerService(db)
    success, reason, trips = await scheduler.run_daily_plan(target_date="2026-10-18")

    assert success is True
    assert reason == "success"
    assert len(trips) == 2


def test_generated_excel_template_contains_sunday(tmp_path):
    path = ExcelService.generate_template(str(tmp_path / "students.xlsx"))
    workbook = openpyxl.load_workbook(path, read_only=True)
    headers = [cell.value for cell in workbook.active[1]]

    assert headers[-1] == "Yakshanba"
