import pytest
import os
import uuid
import openpyxl
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, User, Message

from database.db import Database
from services.excel_service import ExcelService
from handlers.parent import cb_student_absent
from handlers.admin import PENDING_EXCEL_IMPORTS, cb_excel_save, cb_excel_cancel
from models.enums import TripItemStatus

@pytest.mark.asyncio
async def test_phase_8_absence_and_excel_import():
    test_db = f"test_phase8_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    test_excel_file = f"test_students_{uuid.uuid4().hex[:8]}.xlsx"

    try:
        await db.init_db()

        mock_bot = AsyncMock()

        parent_tg_id = 55115511
        driver_tg_id = 77117711
        admin_tg_id = 6273865667

        # 1. Boshlang'ich haydovchi, ota-ona va o'quvchilar
        driver_id = await db.add_driver(full_name="Haydovchi Akmal", phone="+998901112233", telegram_id=driver_tg_id)
        assert driver_id > 0

        parent_id = await db.add_parent(telegram_id=parent_tg_id, phone="+998905551122")
        assert parent_id > 0

        st1_id = await db.add_student("Bekzod Usmonov", village_id=1, parent_phone_1="905551122")
        st2_id = await db.add_student("Kamola Usmonova", village_id=1, parent_phone_1="905551122")
        assert st1_id > 0 and st2_id > 0

        await db.link_student_parent(st1_id, parent_id)
        await db.link_student_parent(st2_id, parent_id)

        today_str = datetime.now().strftime("%Y-%m-%d")

        # Reys yaratish: 2 ta bola bilan (biri pending, biri picked_up)
        conn = await db.get_connection()
        cur_t = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_center', ?, '08:00', 'on_way', 1)
        """, (today_str, driver_id))
        trip_id = cur_t.lastrowid

        # st1 hali olinmagan (pending)
        cur_i1 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (trip_id, st1_id))
        item1_id = cur_i1.lastrowid

        # st2 allaqachon olingan (picked_up)
        cur_i2 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status, picked_at)
            VALUES (?, ?, 1, 'picked_up', '07:45:00')
        """, (trip_id, st2_id))
        item2_id = cur_i2.lastrowid

        await conn.commit()
        await conn.close()

        # =============================================================
        # 1. OTA-ONA "BUGUN KELMAYDI" BOSISHI VA TRIP_ITEMS 'ABSENT' BO'LISHI
        # =============================================================
        parent_cb1 = MagicMock(spec=CallbackQuery)
        parent_cb1.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        parent_cb1.bot = mock_bot
        parent_cb1.data = f"absent_btn:{st1_id}"
        parent_cb1.answer = AsyncMock()

        await cb_student_absent(parent_cb1, db)
        parent_cb1.answer.assert_called_with("Farzandingiz bugun kelmaydi deb belgilandi.", show_alert=True)

        # Bazada absences jadvaliga yozilganini tekshirish
        conn = await db.get_connection()
        cur_abs = await conn.execute("SELECT * FROM absences WHERE student_id = ? AND date = ?", (st1_id, today_str))
        abs_row = await cur_abs.fetchone()
        assert abs_row is not None
        assert abs_row["reported_by_role"] == "parent"
        assert abs_row["reported_by_id"] == parent_tg_id

        # trip_items holati 'absent' bo'lganligini tekshirish
        cur_ti1 = await conn.execute("SELECT status FROM trip_items WHERE id = ?", (item1_id,))
        assert (await cur_ti1.fetchone())[0] == TripItemStatus.ABSENT.value
        await conn.close()

        # Haydovchiga xabar yuborilganligini tekshirish
        assert mock_bot.send_message.call_count >= 1
        driver_call_args = mock_bot.send_message.call_args[1]
        assert driver_call_args["chat_id"] == driver_tg_id
        assert "Bekzod Usmonov" in driver_call_args["text"]
        assert "bugun kelmaydi" in driver_call_args["text"]

        # Takroriy bosishda alert berilishi
        parent_cb1.answer.reset_mock()
        await cb_student_absent(parent_cb1, db)
        parent_cb1.answer.assert_called_with("Farzandingiz bugun kelmaydi deb allaqachon belgilangan!", show_alert=True)

        # =============================================================
        # 2. BOLA ALLAQACHON MASHINADA (PICKED_UP) BO'LSA RAD ETILISHI
        # =============================================================
        parent_cb2 = MagicMock(spec=CallbackQuery)
        parent_cb2.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        parent_cb2.bot = mock_bot
        parent_cb2.data = f"absent_btn:{st2_id}"
        parent_cb2.answer = AsyncMock()

        await cb_student_absent(parent_cb2, db)
        parent_cb2.answer.assert_called_with(
            "Farzandingiz allaqachon mashinaga chiqqan, uni bekor qilib bo'lmaydi.",
            show_alert=True
        )

        # Absences jadvaliga yozilmaganligi va item2 holati picked_up bo'lib qolgani
        conn = await db.get_connection()
        cur_abs2 = await conn.execute("SELECT * FROM absences WHERE student_id = ? AND date = ?", (st2_id, today_str))
        assert await cur_abs2.fetchone() is None
        cur_ti2 = await conn.execute("SELECT status FROM trip_items WHERE id = ?", (item2_id,))
        assert (await cur_ti2.fetchone())[0] == TripItemStatus.PICKED_UP.value
        await conn.close()

        # =============================================================
        # 3. EXCEL SHABLON GENERATSIYASI
        # =============================================================
        tpl_path = ExcelService.generate_template("test_shablon.xlsx")
        assert os.path.exists(tpl_path)
        wb_check = openpyxl.load_workbook(tpl_path)
        sheet_check = wb_check.active
        headers = [cell.value for cell in sheet_check[1]]
        assert "F.I.Sh" in headers
        assert "Mahalla" in headers
        assert "Ota telefoni" in headers
        assert "Dushanba" in headers
        os.remove(tpl_path)

        # =============================================================
        # 4. EXCEL FAYLNI PARSLASH VA XATOLIKLARNI AJRATISH
        # =============================================================
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([
            "F.I.Sh", "Mahalla", "Ota telefoni", "Ona telefoni",
            "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba"
        ])
        # 1-qator: To'g'ri yangi o'quvchi ("4-maktab" maktab nomi bilan)
        ws.append(["Nodir Zokirov", "4-maktab", "+998909876543", "", "09:00-11:00", "", "09:00-11:00", "", "", ""])
        # 2-qator: Noto'g'ri telefon
        ws.append(["Sardor Xato", "Arpaariq", "12345", "", "09:00-11:00", "", "", "", "", ""])
        # 3-qator: Noto'g'ri mahalla
        ws.append(["Botir Xato", "Marsliklar", "901112233", "", "09:00-11:00", "", "", "", "", ""])
        # 4-qator: Noto'g'ri dars vaqti
        ws.append(["Jasur Xato", "Arpaariq", "903334455", "", "not-a-time", "", "", "", "", ""])
        wb.save(test_excel_file)

        preview = await ExcelService.parse_and_preview_excel(test_excel_file, db)
        assert preview["new_count"] == 1
        assert preview["update_count"] == 0
        assert preview["error_count"] == 3
        assert len(preview["valid_records"]) == 1
        assert preview["valid_records"][0]["full_name"] == "Nodir Zokirov"
        assert preview["valid_records"][0]["village_id"] == 1  # 4-maktab -> Arpaariq (id=1)

        # =============================================================
        # 5. OLDINDAN KO'RISH VA ADMIN TASDIQLASHDAN SO'NG BAZAGA YOZILISHI
        # =============================================================
        batch_id = "test_batch_123"
        PENDING_EXCEL_IMPORTS[batch_id] = preview["valid_records"]

        admin_cb = MagicMock(spec=CallbackQuery)
        admin_cb.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        admin_cb.data = f"excel_save:{batch_id}"
        admin_cb.message = MagicMock(spec=Message)
        admin_cb.message.edit_text = AsyncMock()
        admin_cb.answer = AsyncMock()

        await cb_excel_save(admin_cb, db)
        admin_cb.answer.assert_called_with("Saqlandi!")

        # Bazada o'quvchi va uning jadvali saqlanganligini tekshirish
        conn = await db.get_connection()
        cur_nodir = await conn.execute("SELECT * FROM students WHERE full_name = 'Nodir Zokirov'")
        nodir_row = await cur_nodir.fetchone()
        assert nodir_row is not None
        assert nodir_row["parent_phone_1"] == "+998909876543"
        nodir_id = nodir_row["id"]

        cur_sch = await conn.execute("SELECT * FROM schedules WHERE student_id = ? ORDER BY weekday ASC", (nodir_id,))
        sch_rows = [dict(r) for r in await cur_sch.fetchall()]
        assert len(sch_rows) == 2  # Dushanba (0) va Chorshanba (2)
        assert sch_rows[0]["weekday"] == 0 and sch_rows[0]["start_time"] == "09:00"
        assert sch_rows[1]["weekday"] == 2 and sch_rows[1]["start_time"] == "09:00"
        await conn.close()

        # 6. QAYTA IMPORT QILISHDA UPSERT (UPDATE) ISHLASHI
        preview_again = await ExcelService.parse_and_preview_excel(test_excel_file, db)
        assert preview_again["new_count"] == 0
        assert preview_again["update_count"] == 1  # Endi mavjud, shuning uchun update_count=1

        # Bekor qilish (cancel) callback testi
        batch_id_cancel = "cancel_batch_456"
        PENDING_EXCEL_IMPORTS[batch_id_cancel] = preview_again["valid_records"]
        cancel_cb = MagicMock(spec=CallbackQuery)
        cancel_cb.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        cancel_cb.data = f"excel_cancel:{batch_id_cancel}"
        cancel_cb.message = MagicMock(spec=Message)
        cancel_cb.message.edit_text = AsyncMock()
        cancel_cb.answer = AsyncMock()

        await cb_excel_cancel(cancel_cb)
        cancel_cb.answer.assert_called_with("Bekor qilindi.")
        assert batch_id_cancel not in PENDING_EXCEL_IMPORTS

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
        if os.path.exists(test_excel_file):
            os.remove(test_excel_file)
