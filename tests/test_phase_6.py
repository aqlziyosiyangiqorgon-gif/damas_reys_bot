import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, CallbackQuery

from database.db import Database
from handlers.staff import (
    cmd_staff_center,
    cmd_staff_home,
    cb_staff_accept,
    cb_staff_accept_all,
    cb_staff_handover,
    cb_staff_handover_all,
)
from models.enums import UserRole, TripStatus, TripItemStatus

@pytest.mark.asyncio
async def test_phase_6_staff_flow():
    test_db = f"test_phase6_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        staff_tg_id = 88888888
        non_staff_tg_id = 99999999
        parent_tg_id = 77777777

        # 1. Boshlang'ich ma'lumotlar
        staff_id = await db.add_staff(full_name="Oila xodimi", phone="+998908888888", telegram_id=staff_tg_id)
        assert staff_id > 0

        driver_id = await db.add_driver(full_name="Haydovchi Olti", phone="+998906666666", telegram_id=66666666)
        assert driver_id > 0

        # 3 ta o'quvchi
        st1_id = await db.add_student("O'quvchi Bir", village_id=1, parent_phone_1="90 777 77 77")
        st2_id = await db.add_student("O'quvchi Ikki", village_id=1, parent_phone_1="90 777 77 77")
        st3_id = await db.add_student("O'quvchi Uch", village_id=1, parent_phone_1="90 777 77 77")

        # Ota-onani ro'yxatdan o'tkazish va bolalarni bog'lash
        parent_id = await db.add_parent(telegram_id=parent_tg_id, phone="+998907777777")
        await db.link_student_parent(st1_id, parent_id)
        await db.link_student_parent(st2_id, parent_id)
        await db.link_student_parent(st3_id, parent_id)

        today_str = datetime.now().strftime("%Y-%m-%d")
        conn = await db.get_connection()

        # Trip A: to_center (boshida on_way - hali markazga yetmagan)
        cur_a = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_center', ?, '08:00', 'on_way', 1)
        """, (today_str, driver_id))
        trip_center_id = cur_a.lastrowid

        cur_it1 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'picked_up')
        """, (trip_center_id, st1_id))
        item1_center_id = cur_it1.lastrowid

        cur_it2 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'picked_up')
        """, (trip_center_id, st2_id))
        item2_center_id = cur_it2.lastrowid

        # Trip B: to_home (rejalashtirilgan)
        cur_b = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
            VALUES (?, 'to_home', ?, '13:00', 'approved', 1)
        """, (today_str, driver_id))
        trip_home_id = cur_b.lastrowid

        cur_it3 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (trip_home_id, st1_id))
        item1_home_id = cur_it3.lastrowid

        cur_it4 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (trip_home_id, st2_id))
        item2_home_id = cur_it4.lastrowid

        await conn.commit()
        await conn.close()

        mock_bot = AsyncMock()

        # =============================================================
        # 1. RUXSAT VA XAVFSIZLIK TEKSHIRUVI
        # =============================================================
        non_staff_msg = MagicMock(spec=Message)
        non_staff_msg.from_user = User(id=non_staff_tg_id, is_bot=False, first_name="Begona")
        non_staff_msg.answer = AsyncMock()

        # Xodim bo'lmagan foydalanuvchi /markaz va /topshirish ga uringanda rad etilishi
        await cmd_staff_center(non_staff_msg, db, user_role=UserRole.PARENT)
        non_staff_msg.answer.assert_called_once_with("Ushbu buyruq faqat markaz xodimlari uchun!")

        non_staff_msg.answer.reset_mock()
        await cmd_staff_home(non_staff_msg, db, user_role=UserRole.DRIVER)
        non_staff_msg.answer.assert_called_once_with("Ushbu buyruq faqat markaz xodimlari uchun!")

        # Begona callback chaqirganda
        stranger_cb = MagicMock(spec=CallbackQuery)
        stranger_cb.from_user = User(id=non_staff_tg_id, is_bot=False, first_name="Begona")
        stranger_cb.answer = AsyncMock()
        stranger_cb.data = f"staff_accept:{trip_center_id}:{item1_center_id}"

        await cb_staff_accept(stranger_cb, db)
        stranger_cb.answer.assert_called_once_with("Faqat markaz xodimlari uchun!", show_alert=True)

        # =============================================================
        # 2. MARKAZGA YETMAGAN REYSDAN QABUL QILIB BO'LMASLIGI
        # =============================================================
        staff_cb = MagicMock(spec=CallbackQuery)
        staff_cb.from_user = User(id=staff_tg_id, is_bot=False, first_name="Xodim")
        staff_cb.bot = mock_bot
        staff_cb.message = MagicMock(spec=Message)
        staff_cb.message.edit_text = AsyncMock()
        staff_cb.answer = AsyncMock()

        staff_cb.data = f"staff_accept:{trip_center_id}:{item1_center_id}"
        await cb_staff_accept(staff_cb, db)
        staff_cb.answer.assert_called_with("Reys hali markazga yetib kelmagan!", show_alert=True)
        it1_check = await db.get_trip_item_by_id(item1_center_id)
        assert it1_check["status"] == "picked_up"

        # Hammasini qabul qilish ham rad etilishi kerak
        staff_cb.answer.reset_mock()
        staff_cb.data = f"staff_accept_all:{trip_center_id}"
        await cb_staff_accept_all(staff_cb, db)
        staff_cb.answer.assert_called_with("Reys hali markazga yetib kelmagan!", show_alert=True)

        # =============================================================
        # 3. REYS AT_CENTER BO'LGACH QABUL QILISH (TO_CENTER)
        # =============================================================
        await db.update_trip_status(trip_center_id, "at_center")

        # 1-o'quvchini alohida qabul qilish (cb_staff_accept)
        staff_cb.answer.reset_mock()
        staff_cb.data = f"staff_accept:{trip_center_id}:{item1_center_id}"
        await cb_staff_accept(staff_cb, db)
        staff_cb.answer.assert_called_with("O'quvchi darsga qabul qilindi!")

        it1_acc = await db.get_trip_item_by_id(item1_center_id)
        assert it1_acc["status"] == TripItemStatus.ACCEPTED.value
        assert it1_acc["accepted_at"] is not None

        # Ota-onaga bildirishnoma borishi (notify_log)
        conn = await db.get_connection()
        cur_notif = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (st1_id,))
        notifs = [dict(r) for r in await cur_notif.fetchall()]
        await conn.close()
        assert len(notifs) >= 1
        assert "Farzandingiz darsga kirdi." in notifs[0]["text"]
        assert notifs[0]["parent_id"] == parent_id

        # 1-o'quvchini qayta qabul qilishga urinish -> Allaqachon bajarilgan
        staff_cb.answer.reset_mock()
        await cb_staff_accept(staff_cb, db)
        staff_cb.answer.assert_called_with("Allaqachon bajarilgan!", show_alert=True)

        # 2-o'quvchini "Hammasini qabul qilish" orqali qabul qilish (cb_staff_accept_all)
        staff_cb.answer.reset_mock()
        staff_cb.data = f"staff_accept_all:{trip_center_id}"
        await cb_staff_accept_all(staff_cb, db)
        staff_cb.answer.assert_called_with("Barcha o'quvchilar darsga qabul qilindi!")

        it2_acc = await db.get_trip_item_by_id(item2_center_id)
        assert it2_acc["status"] == TripItemStatus.ACCEPTED.value
        assert it2_acc["accepted_at"] is not None

        # 2-bola uchun ham bildirishnoma borishi
        conn = await db.get_connection()
        cur_notif2 = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (st2_id,))
        notifs2 = [dict(r) for r in await cur_notif2.fetchall()]
        await conn.close()
        assert len(notifs2) >= 1
        assert "Farzandingiz darsga kirdi." in notifs2[0]["text"]

        # Hamma bolalar qabul qilingani uchun reys DONE bo'lishi kerak
        trip_c_done = await db.get_trip_details(trip_center_id)
        assert trip_c_done["status"] == TripStatus.DONE.value
        assert trip_c_done["finished_at"] is not None

        # Yana bir marta accept_all bosilsa -> Allaqachon bajarilgan
        staff_cb.answer.reset_mock()
        await cb_staff_accept_all(staff_cb, db)
        staff_cb.answer.assert_called_with("Allaqachon bajarilgan!", show_alert=True)

        # =============================================================
        # 4. TOPSHIRISH OQIMI (TO_HOME)
        # =============================================================
        # 1-bolani alohida topshirish
        staff_cb.answer.reset_mock()
        staff_cb.data = f"staff_handover:{trip_home_id}:{item1_home_id}"
        await cb_staff_handover(staff_cb, db)
        staff_cb.answer.assert_called_with("O'quvchi haydovchiga topshirildi!")

        it1_home = await db.get_trip_item_by_id(item1_home_id)
        assert it1_home["status"] == TripItemStatus.HANDED_OVER.value
        assert it1_home["handed_at"] is not None

        # Ota-onaga "Dars tugadi, farzandingiz haydovchiga topshirildi" xabari
        conn = await db.get_connection()
        cur_notif_h = await conn.execute("SELECT * FROM notify_log WHERE student_id = ? ORDER BY id DESC", (st1_id,))
        notifs_h = [dict(r) for r in await cur_notif_h.fetchall()]
        await conn.close()
        assert "Dars tugadi, farzandingiz haydovchiga topshirildi." in notifs_h[0]["text"]

        # Reys holati handed_over ga o'tganligini tekshirish
        trip_h_check = await db.get_trip_details(trip_home_id)
        assert trip_h_check["status"] == TripStatus.HANDED_OVER.value

        # Takroriy topshirish -> Allaqachon bajarilgan
        staff_cb.answer.reset_mock()
        await cb_staff_handover(staff_cb, db)
        staff_cb.answer.assert_called_with("Allaqachon bajarilgan!", show_alert=True)

        # 2-bolani "Hammasini topshirish" orqali topshirish
        staff_cb.answer.reset_mock()
        staff_cb.data = f"staff_handover_all:{trip_home_id}"
        await cb_staff_handover_all(staff_cb, db)
        staff_cb.answer.assert_called_with("Barcha o'quvchilar haydovchiga topshirildi!")

        it2_home = await db.get_trip_item_by_id(item2_home_id)
        assert it2_home["status"] == TripItemStatus.HANDED_OVER.value

        # Takroriy topshirish_all -> Allaqachon bajarilgan
        staff_cb.answer.reset_mock()
        await cb_staff_handover_all(staff_cb, db)
        staff_cb.answer.assert_called_with("Allaqachon bajarilgan!", show_alert=True)

        # =============================================================
        # 5. AUDIT LOG (event_log) TEKSHIRUVI
        # =============================================================
        conn = await db.get_connection()
        cur_ev = await conn.execute("SELECT * FROM event_log WHERE actor_role='staff' ORDER BY id ASC")
        events = [dict(r) for r in await cur_ev.fetchall()]
        await conn.close()

        actions = [e["action"] for e in events]
        assert "accept_student" in actions
        assert "handover_student" in actions
        for e in events:
            assert e["actor_role"] == "staff"
            assert e["actor_id"] == staff_tg_id

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)


@pytest.mark.asyncio
async def test_handover_does_not_regress_on_way_trip(tmp_path):
    db = Database(str(tmp_path / "handover_on_way.db"))
    await db.init_db()

    staff_id = await db.add_staff(
        telegram_id=88880001,
        full_name="Oila xodimi",
        phone="+998908888001",
    )
    driver_id = await db.add_driver(
        telegram_id=66660001,
        full_name="Haydovchi",
        phone="+998906666001",
    )
    student_id = await db.add_student(
        "Sinov o'quvchisi",
        village_id=1,
        parent_phone_1="+998901112233",
    )

    conn = await db.get_connection()
    trip_cur = await conn.execute("""
        INSERT INTO trips (date, direction, driver_id, planned_time, status, approved)
        VALUES ('2026-10-21', 'to_home', ?, '13:00', 'on_way', 1)
    """, (driver_id,))
    trip_id = trip_cur.lastrowid
    item_cur = await conn.execute("""
        INSERT INTO trip_items (trip_id, student_id, village_id, status)
        VALUES (?, ?, 1, 'pending')
    """, (trip_id, student_id))
    item_id = item_cur.lastrowid
    await conn.commit()
    await conn.close()

    result = await db.handover_student(item_id, staff_id)

    assert result[1] == "success"
    trip = await db.get_trip_details(trip_id)
    item = await db.get_trip_item_by_id(item_id)
    assert trip["status"] == "on_way"
    assert item["status"] == "handed_over"
