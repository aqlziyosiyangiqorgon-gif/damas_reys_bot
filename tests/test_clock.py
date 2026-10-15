from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import utils.clock as clock
import services.scheduler_service as scheduler_module
from config import settings
from services.scheduler_service import SchedulerService


def test_now_tz_is_naive_and_uses_tashkent_at_0030(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 10, 13, 19, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant

    monkeypatch.setattr(clock, "datetime", FrozenDatetime)

    result = clock.now_tz()

    assert result == datetime(2026, 10, 14, 0, 30)
    assert result.tzinfo is None


@pytest.mark.asyncio
async def test_daily_plan_uses_tashkent_date_after_midnight(monkeypatch):
    tashkent_midnight = datetime(2026, 10, 14, 0, 30)
    monkeypatch.setattr(scheduler_module, "now_tz", lambda: tashkent_midnight)
    db = SimpleNamespace(
        check_existing_active_trips=AsyncMock(return_value=False),
        get_day_schedules=AsyncMock(return_value=[]),
        get_absent_student_ids=AsyncMock(return_value=[]),
        get_drivers=AsyncMock(return_value=[]),
        save_generated_trips=AsyncMock(return_value=True),
    )
    scheduler = SchedulerService(db)

    success, reason, trips = await scheduler._run_daily_plan()

    assert (success, reason, trips) == (True, "success", [])
    db.check_existing_active_trips.assert_awaited_once_with("2026-10-14")
    db.get_day_schedules.assert_awaited_once_with(2)
    db.save_generated_trips.assert_awaited_once_with([], "2026-10-14")


@pytest.mark.asyncio
async def test_scheduler_jobs_have_timezone_and_execution_guards():
    scheduler = SchedulerService(SimpleNamespace())

    try:
        scheduler.start()
        jobs = {job.id: job for job in scheduler.scheduler.get_jobs()}

        assert set(jobs) == {"daily_plan", "auto_approve", "reminders", "backup_db"}
        for job in jobs.values():
            assert job.misfire_grace_time == 600
            assert job.coalesce is True
            assert job.max_instances == 1

        daily_fields = {field.name: str(field) for field in jobs["daily_plan"].trigger.fields}
        approve_fields = {field.name: str(field) for field in jobs["auto_approve"].trigger.fields}
        plan_hour, plan_minute = settings.DAILY_PLAN_TIME.split(":")
        approve_hour, approve_minute = settings.AUTO_APPROVE_TIME.split(":")
        assert daily_fields["hour"] == str(int(plan_hour))
        assert daily_fields["minute"] == str(int(plan_minute))
        assert approve_fields["hour"] == str(int(approve_hour))
        assert approve_fields["minute"] == str(int(approve_minute))
        assert jobs["daily_plan"].trigger.timezone == ZoneInfo("Asia/Tashkent")
    finally:
        scheduler.stop()
