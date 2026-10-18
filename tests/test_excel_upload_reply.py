from shutil import copyfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

import handlers.admin as admin_handlers
from database.db import Database
from services.excel_service import ExcelService


def _upload_message(file_name, *, file_size=100, download=None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=6273865667),
        document=SimpleNamespace(file_name=file_name, file_size=file_size),
        bot=SimpleNamespace(download=download or AsyncMock()),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_xls_upload_replies_xlsx_only(monkeypatch):
    monkeypatch.setattr(admin_handlers, "is_admin", lambda _user_id: True)
    message = _upload_message("students.xls")

    await admin_handlers.process_admin_excel_upload(message, db=None)

    message.answer.assert_awaited_once_with("Faqat .xlsx formatidagi fayl qabul qilinadi.")
    message.bot.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_xlsx_upload_returns_preview_and_save_button(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_handlers, "is_admin", lambda _user_id: True)
    db = Database(str(tmp_path / "upload_preview.db"))
    await db.init_db()
    village = next(v for v in await db.get_villages() if v["name"] == "Arpaariq")

    source_path = tmp_path / "one_student.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([
        "F.I.Sh", "Mahalla", "Ota telefoni", "Ona telefoni",
        "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba",
    ])
    sheet.append([
        "Excel Preview Test", village["name"], "+998901112233", "",
        "09:00-11:00", "", "", "", "", "",
    ])
    workbook.save(source_path)

    async def fake_download(_document, destination):
        copyfile(source_path, destination)
        return destination

    message = _upload_message("students.xlsx", download=AsyncMock(side_effect=fake_download))
    await admin_handlers.process_admin_excel_upload(message, db)

    message.answer.assert_awaited_once()
    preview_text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "Fayl tahlili" in preview_text
    assert "Yangi o'quvchilar: <b>1 ta</b>" in preview_text
    assert any(
        button.text == "✅ Bazaga saqlash"
        for row in keyboard.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_unreadable_xlsx_replies_with_template_message(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_handlers, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(
        ExcelService,
        "parse_and_preview_excel",
        AsyncMock(side_effect=RuntimeError("private file contents must not be logged")),
    )

    async def fake_download(_document, destination):
        with open(destination, "wb") as file:
            file.write(b"not a valid workbook")
        return destination

    message = _upload_message("broken.xlsx", download=AsyncMock(side_effect=fake_download))
    await admin_handlers.process_admin_excel_upload(message, db=None)

    message.answer.assert_awaited_once_with("Faylni o'qib bo'lmadi. Shablondan foydalaning.")
