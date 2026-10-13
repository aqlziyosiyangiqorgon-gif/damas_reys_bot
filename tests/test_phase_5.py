import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, CallbackQuery

from database.db import Database
from handlers.driver import (
    cmd_driver_trips,
    cb_driver_start_trip,
    cb_driver_pickup,
    cb_driver_arrive_center,
    cb_driver_dropoff,
    cb_driver_finish_trip,
)
from models.enums import UserRole, TripDirection, TripStatus, TripItemStatus

@pytest.mark.asyncio
async def test_phase_5_driver_flow():
    test_db = f"test_phase5_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        # 1. Boshlang'ich ma'lumotlar: 2 ta haydovchi, 1 ta o'quvchi, 1 ta ota-ona
        driver1_tg_id = 11111111
        driver2_tg_id = 22222222
        parent_tg_id = 33333333

        driver1_id = await db.add_driver(full_name="Haydovchi Bir", phone="+998901111111", telegram_id=driver1_tg_id)
        driver2_id = await db.add_driver(full_name="Haydovchi Ikki", phone="+998902222222", telegram_id=driver2_tg_id)
        assert driver1_id > 0 and driver2_id > 0

        # O'quvchi
        student_id = await db.add_student(
            full_name="Sardor Rahimiy",
            village_id=1,  # Arpaariq (travel_minutes=25)
            parent_phone_1="90 333 33 33"
        )
        assert student_id > 0

        # Ota-onani ro'yxatdan o'tkazish va bolani bog'lash
        parent_id = await db.add_parent(telegram_id=parent_tg_id, phone="+998903333333")
        assert parent_id > 0
        await db.link_student_parent(student_id, parent_id)

        # 2 ta reys yaratish: 1) to_center (Driver 1), 2) to_home (Driver 1)
        today_str = datetime.now().strftime("%Y-%m-%d")
        conn = await db.get_connection()
        
        # Trip 1: to_center (approved)
        cur1 = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_center', ?, '07:30', 'approved', 1)
        """, (today_str, driver1_id))
        trip_center_id = cur1.lastrowid
        cur_item1 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (trip_center_id, student_id))
        item_center_id = cur_item1.lastrowid

        # Trip 2: to_home (handed_over)
        cur2 = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_home', ?, '12:30', 'handed_over', 1)
        """, (today_str, driver1_id))
        trip_home_id = cur2.lastrowid
        cur_item2 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'handed_over')
        """, (trip_home_id, student_id))
        item_home_id = cur_item2.lastrowid

        await conn.commit()
        await conn.close()

        # Mock bot
        mock_bot = AsyncMock()

        # =============================================================
        # A. RUXSAT VA XAVFSIZLIK: Haydovchi bo'lmagan foydalanuvchi buyruq ishlata olmaydi
        # =============================================================
        non_driver_msg = MagicMock(spec=Message)
        non_driver_msg.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        non_driver_msg.answer = AsyncMock()

        await cmd_driver_trips(non_driver_msg, db, user_role=UserRole.PARENT)
        non_driver_msg.answer.assert_called_once_with("Ushbu buyruq faqat haydovchilar uchun!")

        # To'g'ri haydovchi o'z reyslarini ko'rishi
        driver1_msg = MagicMock(spec=Message)
        driver1_msg.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Haydovchi")
        driver1_msg.answer = AsyncMock()

        await cmd_driver_trips(driver1_msg, db, user_role=UserRole.DRIVER)
        assert driver1_msg.answer.call_count >= 2  # Sarlavha + kamida 2 ta reys kartasi

        # =============================================================
        # B. BEGONA HAYDOVCHI TEKSHIRUVI (Driver 2, Driver 1 reysiga urinsa rad etiladi)
        # =============================================================
        stranger_cb = MagicMock(spec=CallbackQuery)
        stranger_cb.from_user = User(id=driver2_tg_id, is_bot=False, first_name="Begona Haydovchi")
        stranger_cb.bot = mock_bot
        stranger_cb.message = MagicMock(spec=Message)
        stranger_cb.message.edit_text = AsyncMock()
        stranger_cb.answer = AsyncMock()

        # 1) Begona yo'lga chiqishga urinsa
        stranger_cb.data = f"driver_start_trip:{trip_center_id}"
        await cb_driver_start_trip(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)
        t_check = await db.get_trip_details(trip_center_id)
        assert t_check["status"] == "approved"  # Holat o'zgarmadi

        # 2) Begona bolani olishga urinsa
        stranger_cb.data = f"driver_pickup:{trip_center_id}:{item_center_id}"
        await cb_driver_pickup(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)

        # 3) Begona markazga yetdim deyishga urinsa
        stranger_cb.data = f"driver_arrive_center:{trip_center_id}"
        await cb_driver_arrive_center(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)

        # 4) Begona bolani uyga yetkazishga urinsa
        stranger_cb.data = f"driver_dropoff:{trip_home_id}:{item_home_id}"
        await cb_driver_dropoff(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)

        # 5) Begona reysni yakunlashga urinsa
        stranger_cb.data = f"driver_finish_trip:{trip_home_id}"
        await cb_driver_finish_trip(stranger_cb, db)
        stranger_cb.answer.assert_called_with("Bu sizning reysingiz emas!", show_alert=True)

        # =============================================================
        # C. TO_CENTER OQIMI (Driver 1: start_trip -> pickup -> arrive_center)
        # =============================================================
        driver1_cb = MagicMock(spec=CallbackQuery)
        driver1_cb.from_user = User(id=driver1_tg_id, is_bot=False, first_name="Haydovchi Bir")
        driver1_cb.bot = mock_bot
        driver1_cb.message = MagicMock(spec=Message)
        driver1_cb.message.edit_text = AsyncMock()
        driver1_cb.answer = AsyncMock()

        # 1. [🚗 Yo'ldaman]
        driver1_cb.data = f"driver_start_trip:{trip_center_id}"
        await cb_driver_start_trip(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Reys boshlandi! Oq yo'l!")
        t_started = await db.get_trip_details(trip_center_id)
        assert t_started["status"] == TripStatus.ON_WAY.value
        assert t_started["started_at"] is not None

        # Takroriy bosish tekshiruvi:
        driver1_cb.answer.reset_mock()
        await cb_driver_start_trip(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Reys allaqachon yo'lda!", show_alert=True)

        # 2. [✅ Oldim]
        driver1_cb.answer.reset_mock()
        driver1_cb.data = f"driver_pickup:{trip_center_id}:{item_center_id}"
        await cb_driver_pickup(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Sardor Rahimiy olindi!")
        
        item_picked = await db.get_trip_item_by_id(item_center_id)
        assert item_picked["status"] == TripItemStatus.PICKED_UP.value
        assert item_picked["picked_at"] is not None

        # Ota-onaga bildirishnoma logi tekshiruvi (notify_log)
        conn = await db.get_connection()
        cur_notif = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (student_id,))
        notifs = [dict(r) for r in await cur_notif.fetchall()]
        await conn.close()
        assert len(notifs) >= 1
        assert "Farzandingiz mashinaga o'tirdi" in notifs[0]["text"]
        assert notifs[0]["parent_id"] == parent_id

        # Takroriy [Oldim] bosish
        driver1_cb.answer.reset_mock()
        await cb_driver_pickup(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Ushbu bola allaqachon olingan!", show_alert=True)

        # 3. [🏁 Yetib keldim]
        driver1_cb.answer.reset_mock()
        driver1_cb.data = f"driver_arrive_center:{trip_center_id}"
        await cb_driver_arrive_center(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Markazga yetib kelindi!")

        t_arrived = await db.get_trip_details(trip_center_id)
        assert t_arrived["status"] == TripStatus.AT_CENTER.value

        # Ota-onaga yetib kelganlik bildirishnomasi
        conn = await db.get_connection()
        cur_notif = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (student_id,))
        notifs_arrive = [dict(r) for r in await cur_notif.fetchall()]
        await conn.close()
        assert len(notifs_arrive) >= 2
        assert "xavfsiz va salomat yetib keldi" in notifs_arrive[0]["text"]

        # Takroriy yetib keldim bosish
        driver1_cb.answer.reset_mock()
        await cb_driver_arrive_center(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Allaqachon markazga yetilgan!", show_alert=True)

        # =============================================================
        # D. TO_HOME OQIMI (Driver 1: start_trip -> dropoff -> finish_trip)
        # =============================================================
        # 1. [🚗 Yo'lga chiqdim]
        driver1_cb.answer.reset_mock()
        driver1_cb.data = f"driver_start_trip:{trip_home_id}"
        await cb_driver_start_trip(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Reys boshlandi! Oq yo'l!")
        t_home_started = await db.get_trip_details(trip_home_id)
        assert t_home_started["status"] == TripStatus.ON_WAY.value

        # Ota-onaga yo'lga chiqilgani bildirilishi
        conn = await db.get_connection()
        cur_notif = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (student_id,))
        notifs_home_start = [dict(r) for r in await cur_notif.fetchall()]
        await conn.close()
        assert "markazdan uyga yo'lga chiqdi" in notifs_home_start[0]["text"]

        # 2. [🏠 Yetkazdim]
        driver1_cb.answer.reset_mock()
        driver1_cb.data = f"driver_dropoff:{trip_home_id}:{item_home_id}"
        await cb_driver_dropoff(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Sardor Rahimiy yetkazildi!")

        item_dropped = await db.get_trip_item_by_id(item_home_id)
        assert item_dropped["status"] == TripItemStatus.DROPPED_OFF.value
        assert item_dropped["dropped_at"] is not None

        # Ota-onaga yetkazilganlik bildirishnomasi
        conn = await db.get_connection()
        cur_notif = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (student_id,))
        notifs_dropoff = [dict(r) for r in await cur_notif.fetchall()]
        await conn.close()
        assert "Farzandingiz xavfsiz va salomat yetkazildi" in notifs_dropoff[0]["text"]

        # Takroriy yetkazdim bosish
        driver1_cb.answer.reset_mock()
        await cb_driver_dropoff(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Ushbu bola allaqachon yetkazilgan!", show_alert=True)

        # 3. [🏁 Reysni yakunlash]
        driver1_cb.answer.reset_mock()
        driver1_cb.data = f"driver_finish_trip:{trip_home_id}"
        await cb_driver_finish_trip(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Reys muvaffaqiyatli yakunlandi! Rahmat!")

        t_home_done = await db.get_trip_details(trip_home_id)
        assert t_home_done["status"] == TripStatus.DONE.value
        assert t_home_done["finished_at"] is not None

        # Takroriy yakunlash
        driver1_cb.answer.reset_mock()
        await cb_driver_finish_trip(driver1_cb, db)
        driver1_cb.answer.assert_called_with("Reys allaqachon yakunlangan!", show_alert=True)

        # =============================================================
        # E. AUDIT LOG (event_log) TEKSHIRUVI
        # =============================================================
        conn = await db.get_connection()
        cur_ev = await conn.execute("SELECT * FROM event_log WHERE actor_role='driver' ORDER BY id ASC")
        events = [dict(r) for r in await cur_ev.fetchall()]
        await conn.close()

        actions = [e["action"] for e in events]
        assert "start_trip" in actions
        assert "pickup_student" in actions
        assert "arrive_center" in actions
        assert "dropoff_student" in actions
        assert "finish_trip" in actions

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
