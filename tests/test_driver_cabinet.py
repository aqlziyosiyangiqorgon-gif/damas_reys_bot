import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, CallbackQuery, ReplyKeyboardMarkup

from database.db import Database
from handlers.driver import (
    get_driver_menu_kb,
    build_driver_trip_keyboard,
    cmd_driver_trips,
    driver_center_contact,
    driver_emergency_prompt,
    cb_driver_alert,
    cb_driver_pickup_village,
    cb_driver_dropoff_village,
    cb_driver_start_trip,
)
from models.enums import UserRole, TripDirection, TripStatus, TripItemStatus

@pytest.mark.asyncio
async def test_driver_cabinet_full_flow():
    test_db = f"test_driver_cab_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        # 1. Haydovchi Reply klaviaturasi tekshiruvi
        kb = get_driver_menu_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        buttons = [b.text for row in kb.keyboard for b in row]
        assert "🚐 Bugungi reyslarim" in buttons
        assert "⚠️ Adminga tezkor xabar" in buttons
        assert "📞 Markaz / Admin" in buttons

        # 2. Haydovchilar va O'quvchilarni yaratish
        driver1_tg_id = 77112233
        driver2_tg_id = 88112233
        parent1_tg_id = 99112233
        parent2_tg_id = 99112244

        driver1_id = await db.add_driver(driver1_tg_id, "Haydovchi Botir", "+998901234567")
        driver2_id = await db.add_driver(driver2_tg_id, "Haydovchi Jamshid (Begona)", "+998909876543")

        # Bitta mahallada (village_id=1, Arpaariq) 2 ta bola
        student1_id = await db.add_student("Ali Valiyev", 1, "90 111 11 11")
        student2_id = await db.add_student("Vali Aliyev", 1, "90 222 22 22")

        # Ota-onalarni ro'yxatdan o'tkazish
        p1_id = await db.add_parent(parent1_tg_id, "+998901111111")
        p2_id = await db.add_parent(parent2_tg_id, "+998902222222")
        await db.link_student_parent(student1_id, p1_id)
        await db.link_student_parent(student2_id, p2_id)

        # 3. Bugungi to_center reysi yaratish
        today_str = datetime.now().strftime("%Y-%m-%d")
        conn = await db.get_connection()
        cur_trip = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_center', ?, '07:30', 'on_way', 1)
        """, (today_str, driver1_id))
        trip_to_center_id = cur_trip.lastrowid

        await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending'), (?, ?, 1, 'pending')
        """, (trip_to_center_id, student1_id, trip_to_center_id, student2_id))
        await conn.commit()
        await conn.close()

        # 4. build_driver_trip_keyboard da "✅ Arpaariq: Hammasini oldim" tugmasi chiqishini tekshirish
        trip_data = await db.get_trip_details(trip_to_center_id)
        trip_kb = build_driver_trip_keyboard(trip_data)
        inline_btns = [b.text for row in trip_kb.inline_keyboard for b in row]
        inline_cbs = [b.callback_data for row in trip_kb.inline_keyboard for b in row]

        assert any("Hammasini oldim" in t for t in inline_btns)
        assert f"driver_pickup_village:{trip_to_center_id}:1" in inline_cbs

        mock_bot = AsyncMock()

        # 5. Begona haydovchi (driver2) bu mahallani olishga urinsa rad etilishi
        stranger_cb = MagicMock(spec=CallbackQuery)
        stranger_cb.from_user = User(id=driver2_tg_id, is_bot=False, first_name="Begona")
        stranger_cb.bot = mock_bot
        stranger_cb.message = MagicMock(spec=Message)
        stranger_cb.message.edit_text = AsyncMock()
        stranger_cb.answer = AsyncMock()
        stranger_cb.data = f"driver_pickup_village:{trip_to_center_id}:1"

        await cb_driver_pickup_village(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)

        # 6. Asl haydovchi (driver1) "Hammasini oldim" tugmasini bosishi
        driver1_cb = MagicMock(spec=CallbackQuery)
        driver1_cb.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Botir")
        driver1_cb.bot = mock_bot
        driver1_cb.message = MagicMock(spec=Message)
        driver1_cb.message.edit_text = AsyncMock()
        driver1_cb.answer = AsyncMock()
        driver1_cb.data = f"driver_pickup_village:{trip_to_center_id}:1"

        await cb_driver_pickup_village(driver1_cb, db)
        assert "olindi" in driver1_cb.answer.call_args[0][0]

        # Har ikkala o'quvchi 'picked_up' bo'lganini va notify_log yozilganini tekshirish
        updated_trip = await db.get_trip_details(trip_to_center_id)
        for it in updated_trip["items"]:
            assert it["status"] == "picked_up"

        conn = await db.get_connection()
        cur_logs = await conn.execute("SELECT * FROM notify_log ORDER BY id ASC")
        logs = [dict(r) for r in await cur_logs.fetchall()]
        await conn.close()
        assert len(logs) == 2
        assert all("mashinaga o'tirdi" in l["text"] for l in logs)

        # Takroriy bosilganda xabar berish
        driver1_cb.answer.reset_mock()
        await cb_driver_pickup_village(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Ushbu mahalladagi barcha bolalar allaqachon olingan!", show_alert=True)

        # 7. Qaytish reysi (to_home) va "Hammasini yetkazdim"
        conn = await db.get_connection()
        cur_home = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_home', ?, '12:30', 'on_way', 1)
        """, (today_str, driver1_id))
        trip_to_home_id = cur_home.lastrowid
        await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'handed_over'), (?, ?, 1, 'handed_over')
        """, (trip_to_home_id, student1_id, trip_to_home_id, student2_id))
        await conn.commit()
        await conn.close()

        home_trip_data = await db.get_trip_details(trip_to_home_id)
        home_kb = build_driver_trip_keyboard(home_trip_data)
        home_cbs = [b.callback_data for row in home_kb.inline_keyboard for b in row]
        assert f"driver_dropoff_village:{trip_to_home_id}:1" in home_cbs

        # Yetkazish tugmasini bosish
        driver1_cb.data = f"driver_dropoff_village:{trip_to_home_id}:1"
        driver1_cb.answer.reset_mock()
        await cb_driver_dropoff_village(driver1_cb, db)
        assert "yetkazildi" in driver1_cb.answer.call_args[0][0]

        updated_home = await db.get_trip_details(trip_to_home_id)
        for it in updated_home["items"]:
            assert it["status"] == "dropped_off"

        # 8. Markaz bilan aloqa Reply tugmasi
        msg_contact = MagicMock(spec=Message)
        msg_contact.answer = AsyncMock()
        await driver_center_contact(msg_contact)
        assert "+998 77 260 20 20" in msg_contact.answer.call_args[0][0]

        # 9. Adminga favqulodda tezkor signal yuborish
        msg_alert = MagicMock(spec=Message)
        msg_alert.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Botir")
        msg_alert.answer = AsyncMock()
        await driver_emergency_prompt(msg_alert, db)
        assert msg_alert.answer.called

        # Signal tugmasini bosish (Mashina buzildi)
        alert_cb = MagicMock(spec=CallbackQuery)
        alert_cb.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Botir")
        alert_cb.bot = mock_bot
        alert_cb.message = MagicMock(spec=Message)
        alert_cb.message.edit_text = AsyncMock()
        alert_cb.answer = AsyncMock()
        alert_cb.data = "driver_alert:car_breakdown"

        await cb_driver_alert(alert_cb, db)
        alert_cb.answer.assert_called_with("Xabar yuborildi!", show_alert=True)
        assert mock_bot.send_message.called

        # event_log da favqulodda signal yozilganligini tekshirish
        conn = await db.get_connection()
        cur_ev = await conn.execute("SELECT * FROM event_log WHERE action = 'emergency_alert'")
        ev_row = await cur_ev.fetchone()
        await conn.close()
        assert ev_row is not None
        assert ev_row["actor_role"] == "driver"

        # Bekor qilish tugmasi tekshiruvi
        cancel_cb = MagicMock(spec=CallbackQuery)
        cancel_cb.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Botir")
        cancel_cb.message = MagicMock(spec=Message)
        cancel_cb.message.edit_text = AsyncMock()
        cancel_cb.answer = AsyncMock()
        cancel_cb.data = "driver_alert:cancel"

        await cb_driver_alert(cancel_cb, db)
        cancel_cb.message.edit_text.assert_called_with("❌ Tezkor xabar bekor qilindi.")

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
