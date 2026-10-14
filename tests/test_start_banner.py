from unittest.mock import AsyncMock

import pytest
from aiogram.types import FSInputFile

from utils import start_banner


@pytest.mark.asyncio
async def test_start_banner_falls_back_to_text_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(start_banner, "BANNER_PATH", tmp_path / "banner.jpg")
    message = type("MessageStub", (), {
        "answer": AsyncMock(),
        "answer_photo": AsyncMock(),
    })()

    await start_banner.answer_start(message, "welcome", reply_markup="menu")

    message.answer.assert_awaited_once_with(
        "welcome", reply_markup="menu", parse_mode="HTML"
    )
    message.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_banner_sends_photo_when_present(tmp_path, monkeypatch):
    banner = tmp_path / "banner.jpg"
    banner.write_bytes(b"image placeholder")
    monkeypatch.setattr(start_banner, "BANNER_PATH", banner)
    message = type("MessageStub", (), {
        "answer": AsyncMock(),
        "answer_photo": AsyncMock(),
    })()

    await start_banner.answer_start(message, "welcome", reply_markup="menu")

    message.answer_photo.assert_awaited_once()
    kwargs = message.answer_photo.await_args.kwargs
    assert isinstance(kwargs["photo"], FSInputFile)
    assert kwargs["caption"] == "welcome"
    assert kwargs["reply_markup"] == "menu"
    message.answer.assert_not_awaited()
