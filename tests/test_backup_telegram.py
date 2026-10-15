import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import FSInputFile

import services.scheduler_service as scheduler_module
from config import Settings, settings
from database.db import Database
from services.scheduler_service import SchedulerService


@pytest.mark.asyncio
async def test_backup_is_sent_and_cleanup_uses_filename_date(tmp_path, monkeypatch):
    db_path = tmp_path / "source.db"
    db = Database(str(db_path))
    await db.init_db()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(scheduler_module, "now_tz", lambda: datetime(2026, 10, 14, 1, 2, 3))
    monkeypatch.setattr(settings, "BACKUP_CHAT_ID", -1001234567890)

    expired = backup_dir / "damas_reys_20261006.db"
    exact_cutoff = backup_dir / "damas_reys_20261007.db"
    old_mtime_but_recent_name = backup_dir / "damas_reys_20261013.db"
    unrelated_old = backup_dir / "other_backup_20200101.db"
    for path in (expired, exact_cutoff, old_mtime_but_recent_name, unrelated_old):
        path.write_text("placeholder", encoding="utf-8")
    old_timestamp = datetime(2000, 1, 1).timestamp()
    os.utime(old_mtime_but_recent_name, (old_timestamp, old_timestamp))

    bot = SimpleNamespace(send_document=AsyncMock(return_value=None))
    scheduler = SchedulerService(db, bot=bot)

    created = Path(await scheduler.run_backup_db(backup_dir=str(backup_dir)))

    assert created.is_file()
    assert not expired.exists()
    assert exact_cutoff.is_file()
    assert old_mtime_but_recent_name.is_file()
    assert unrelated_old.is_file()
    sent_call = bot.send_document.await_args
    assert sent_call.kwargs["chat_id"] == -1001234567890
    assert sent_call.kwargs["caption"] == "Zaxira: 2026-10-14"
    assert isinstance(sent_call.kwargs["document"], FSInputFile)
    assert Path(sent_call.kwargs["document"].path) == created


@pytest.mark.asyncio
async def test_failed_telegram_backup_keeps_local_file_and_logs(tmp_path, monkeypatch, caplog):
    db_path = tmp_path / "source.db"
    db = Database(str(db_path))
    await db.init_db()
    monkeypatch.setattr(scheduler_module, "now_tz", lambda: datetime(2026, 10, 14, 12, 0))
    monkeypatch.setattr(settings, "BACKUP_CHAT_ID", None)
    monkeypatch.setattr(settings, "ADMIN_IDS", "123456,654321")
    bot = SimpleNamespace(send_document=AsyncMock(side_effect=TelegramForbiddenError(
        SendMessage(chat_id=123456, text="backup"), "blocked"
    )))
    scheduler = SchedulerService(db, bot=bot)

    created = Path(await scheduler.run_backup_db(backup_dir=str(tmp_path / "backups")))

    assert created.is_file()
    assert bot.send_document.await_args.kwargs["chat_id"] == 123456
    assert "blocked" in caplog.text
    assert "lokal nusxa saqlandi" in caplog.text


def test_backup_chat_id_accepts_empty_or_integer():
    assert Settings(BACKUP_CHAT_ID="").BACKUP_CHAT_ID is None
    assert Settings(BACKUP_CHAT_ID="-1001234567890").BACKUP_CHAT_ID == -1001234567890
