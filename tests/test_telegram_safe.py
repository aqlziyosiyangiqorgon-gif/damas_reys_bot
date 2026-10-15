from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from aiogram.types import ErrorEvent, Update

import services.telegram_safe as telegram_safe
from database.db import Database
from main import log_dispatcher_error
from services.notify_service import NotifyService
from utils.telegram import safe_refresh_edit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "expected_status"),
    [
        (TelegramForbiddenError, "blocked"),
        (TelegramBadRequest, "bad_request"),
        (TelegramNetworkError, "failed"),
        (TelegramRetryAfter, "failed"),
    ],
)
async def test_safe_send_maps_telegram_errors_and_bounds_retries(
    monkeypatch, exception_type, expected_status
):
    method = SendMessage(chat_id=123, text="test")
    if exception_type is TelegramRetryAfter:
        error = exception_type(method, "too many requests", retry_after=2)
    else:
        error = exception_type(method, "failure")
    send_message = AsyncMock(side_effect=error)
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_safe.asyncio, "sleep", sleep)
    bot = SimpleNamespace(send_message=send_message)

    status = await telegram_safe.safe_send(bot, 123, "test")

    assert status == expected_status
    assert send_message.await_count == (1 if expected_status in {"blocked", "bad_request"} else 3)
    if exception_type is TelegramRetryAfter:
        assert [call.args[0] for call in sleep.await_args_list] == [3, 3]
    elif exception_type is TelegramNetworkError:
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_safe_send_retries_rate_limit_and_supports_photo(monkeypatch):
    method = SendMessage(chat_id=123, text="test")
    send_photo = AsyncMock(side_effect=[
        TelegramRetryAfter(method, "retry", retry_after=4),
        None,
    ])
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_safe.asyncio, "sleep", sleep)
    bot = SimpleNamespace(send_photo=send_photo)

    result = await telegram_safe.safe_send(bot, 123, "caption", photo="file-id", parse_mode=None)

    assert result == "sent"
    assert send_photo.await_count == 2
    assert sleep.await_args.args == (5,)
    assert send_photo.await_args.kwargs == {
        "chat_id": 123,
        "photo": "file-id",
        "caption": "caption",
        "parse_mode": None,
    }


@pytest.mark.asyncio
async def test_notify_log_stores_safe_send_status(tmp_path):
    db = Database(str(tmp_path / "notify-status.db"))
    await db.init_db()
    parent_id = await db.add_parent(222001, "+998901234567")
    student_id = await db.add_student("Student", 1, "+998901234567")
    await db.link_student_parent(student_id, parent_id)
    bot = SimpleNamespace(send_message=AsyncMock(
        side_effect=TelegramForbiddenError(SendMessage(chat_id=222001, text="x"), "blocked")
    ))

    log_ids = await NotifyService.send_student_notification(db, student_id, "xabar", bot)

    conn = await db.get_connection()
    cursor = await conn.execute("SELECT status FROM notify_log WHERE id = ?", (log_ids[0],))
    row = await cursor.fetchone()
    await conn.close()
    assert row["status"] == "blocked"


@pytest.mark.asyncio
async def test_refresh_ignores_unchanged_message_and_answers_callback():
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(
            SendMessage(chat_id=1, text="same"), "Bad Request: message is not modified"
        ))),
        answer=AsyncMock(),
    )

    await safe_refresh_edit(callback, "same", None)

    callback.message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once_with("Yangilandi!")


@pytest.mark.asyncio
async def test_dispatcher_error_log_does_not_include_exception_text(caplog):
    event = ErrorEvent(
        update=Update(update_id=55),
        exception=RuntimeError("secret-token-in-error"),
    )

    assert await log_dispatcher_error(event)

    assert "RuntimeError" in caplog.text
    assert "secret-token-in-error" not in caplog.text
