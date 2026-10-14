import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, User, Message
from aiogram.fsm.context import FSMContext

from database.db import Database
from config import settings
from handlers.admin import (
    cb_admin_delay_trip,
    cb_admin_delay_confirm,
    cb_admin_delay_apply,
    cb_admin_delay_cancel,
    cmd_admin_broadcast,
    cb_broadcast_select_audience,
    process_broadcast_content,
    cb_broadcast_send,
    cb_broadcast_cancel,
    PENDING_BROADCASTS,
    DelayStates,
    BroadcastStates
)
from models.enums import TripItemStatus

@pytest.mark.asyncio
async def test_phase_9_delay_and_broadcast():
    test_db = f"test_phase9_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        mock_bot = AsyncMock()

        admin_tg_id = settings.admin_ids_list[0] if settings.admin_ids_list else 6273865667
        non_admin_tg_id = 99998888
        driver_tg_id = 77112233
        staff_tg_id = 66112233
        parent_tg_1 = 55112233
        parent_tg_2 = 55112244

        # 1. Boshlang'ich ma'lumotlarni kiritish
        driver_id = await db.add_driver(full_name="Haydovchi Botir", phone="+998901112233", telegram_id=driver_tg_id)
        assert driver_id > 0

        staff_id = await db.add_staff(full_name="Xodim Dilshod", phone="+998902223344", telegram_id=staff_tg_id)
        assert staff_id > 0

        parent_1_id = await db.add_parent(telegram_id=parent_tg_1, phone="+998903334455")
        parent_2_id = await db.add_parent(telegram_id=parent_tg_2, phone="+998903334466")
        assert parent_1_id > 0 and parent_2_id > 0

        # O'quvchilar: st1 (village 1, zone 1), st2 (village 1, zone 1), st3 (village 4, zone 2)
        st1_id = await db.add_student("O'quvchi Bir", village_id=1, parent_phone_1="903334455")
        st2_id = await db.add_student("O'quvchi Ikki", village_id=1, parent_phone_1="903334466")
        st3_id = await db.add_student("O'quvchi Uch", village_id=4, parent_phone_1="903334455")

        await db.link_student_parent(st1_id, parent_1_id)
        await db.link_student_parent(st2_id, parent_2_id)
        await db.link_student_parent(st3_id, parent_1_id)

        today_str = datetime.now().strftime("%Y-%m-%d")

        # Reys yaratish: st1 (pending), st2 (picked_up)
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

        # st2 allaqachon mashinada (picked_up)
        cur_i2 = await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'picked_up')
        """, (trip_id, st2_id))
        item2_id = cur_i2.lastrowid

        await conn.commit()
        await conn.close()

        # =============================================================
        # 1. KECHIKISH (DELAY) OQIMI VA XAVFSIZLIK TEKSHIRUVI
        # =============================================================

        # 1.1 Non-admin tekshiruvi: begona odam reysni kechiktira olmaydi
        non_admin_cb = MagicMock(spec=CallbackQuery)
        non_admin_cb.from_user = User(id=non_admin_tg_id, is_bot=False, first_name="Hacker")
        non_admin_cb.data = f"admin_delay_trip:{trip_id}"
        non_admin_cb.answer = AsyncMock()

        await cb_admin_delay_trip(non_admin_cb, db)
        non_admin_cb.answer.assert_called_with("Ruxsat yo'q!", show_alert=True)

        # 1.2 Admin kechikish tugmasini bosishi
        admin_cb = MagicMock(spec=CallbackQuery)
        admin_cb.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        admin_cb.data = f"admin_delay_trip:{trip_id}"
        admin_cb.message = MagicMock()
        admin_cb.message.edit_text = AsyncMock()
        admin_cb.answer = AsyncMock()

        await cb_admin_delay_trip(admin_cb, db)
        assert admin_cb.message.edit_text.called
        assert "Kechikish vaqtini tanlang" in admin_cb.message.edit_text.call_args[0][0]

        # 1.3 Admin 15 daqiqani tanlaydi
        admin_cb.data = f"admin_delay_confirm:{trip_id}:15"
        await cb_admin_delay_confirm(admin_cb, db)
        assert "15 daqiqalik kechikish xabari boradi. Yuborilsinmi?" in admin_cb.message.edit_text.call_args[0][0]

        # 1.4 Admin kechikishni tasdiqlaydi [✅ Ha]
        admin_cb.data = f"admin_delay_apply:{trip_id}:15"
        admin_cb.bot = mock_bot
        await cb_admin_delay_apply(admin_cb, db)

        # Bazadagi o'zgarishlarni tekshirish
        trip_updated = await db.get_trip_details(trip_id)
        assert trip_updated["delay_min"] == 15
        assert trip_updated["planned_time"] == "08:15"

        # Haydovchiga xabar yuborilganligini tekshirish
        mock_bot.send_message.assert_any_call(
            chat_id=driver_tg_id,
            text="⚠️ Admin reysni 15 daqiqaga kechiktirdi. Yangi rejalashtirilgan vaqt: <b>08:15</b>",
            parse_mode="HTML"
        )

        # Ota-onalarga alohida matnlar borganini tekshirish:
        # st1 (pending / olinmagan): "Mashina taxminan 15 daqiqaga kechikadi. Noqulaylik uchun uzr."
        mock_bot.send_message.assert_any_call(
            chat_id=parent_tg_1,
            text="Mashina taxminan 15 daqiqaga kechikadi. Noqulaylik uchun uzr."
        )

        # st2 (picked_up / mashinada): "Farzandingiz mashinada, yetib borish taxminan 15 daqiqaga kechikadi."
        mock_bot.send_message.assert_any_call(
            chat_id=parent_tg_2,
            text="Farzandingiz mashinada, yetib borish taxminan 15 daqiqaga kechikadi."
        )

        # Audit log tekshiruvi: event_log da 'trip_delay' yozuvi
        conn = await db.get_connection()
        cur_ev = await conn.execute("SELECT * FROM event_log WHERE action = 'trip_delay' AND trip_id = ?", (trip_id,))
        ev_row = await cur_ev.fetchone()
        assert ev_row is not None
        assert dict(ev_row)["actor_role"] == "admin"
        assert dict(ev_row)["actor_id"] == admin_tg_id
        await conn.close()

        # =============================================================
        # 2. ADMIN XABARLARI (/xabar, BROADCASTS) AUDITORIYA TEKSHIRUVI
        # =============================================================

        # 2.1 Barcha ota-onalar (all)
        aud_all = await db.get_broadcast_audience("all")
        all_tg_ids = {a["telegram_id"] for a in aud_all}
        assert parent_tg_1 in all_tg_ids
        assert parent_tg_2 in all_tg_ids
        assert len(aud_all) == 2

        # 2.2 Mahalla bo'yicha (village 1: Arpaariq)
        aud_v1 = await db.get_broadcast_audience("village", "1")
        v1_tg_ids = {a["telegram_id"] for a in aud_v1}
        assert parent_tg_1 in v1_tg_ids
        assert parent_tg_2 in v1_tg_ids

        # 2.3 Zona bo'yicha (zone 1 vs zone 2)
        aud_z1 = await db.get_broadcast_audience("zone", "1")
        assert len(aud_z1) >= 2
        z1_tg_ids = {a["telegram_id"] for a in aud_z1}
        assert parent_tg_1 in z1_tg_ids
        assert parent_tg_2 in z1_tg_ids

        # 2.4 Bitta o'quvchi bo'yicha (student_id = st2_id)
        aud_st2 = await db.get_broadcast_audience("student", str(st2_id))
        assert len(aud_st2) == 1
        assert aud_st2[0]["telegram_id"] == parent_tg_2

        # 2.5 Haydovchilar (drivers)
        aud_drivers = await db.get_broadcast_audience("drivers")
        assert len(aud_drivers) == 1
        assert aud_drivers[0]["telegram_id"] == driver_tg_id
        assert aud_drivers[0]["role"] == "Haydovchi"

        # 2.6 Xodimlar (staff)
        aud_staff = await db.get_broadcast_audience("staff")
        assert len(aud_staff) == 1
        assert aud_staff[0]["telegram_id"] == staff_tg_id
        assert aud_staff[0]["role"] == "Xodim"

        # =============================================================
        # 3. BROADCAST YUBORISH VA LOGLASH TEKSHIRUVI
        # =============================================================

        mock_bot.send_message.reset_mock()

        # FSM state mocking
        state_data = {
            "bc_aud_type": "drivers",
            "bc_aud_val": None
        }
        mock_state = AsyncMock(spec=FSMContext)
        mock_state.get_data.return_value = state_data

        msg_content = MagicMock(spec=Message)
        msg_content.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        msg_content.text = "E'tibor bering: ertaga darslar soat 08:30 da boshlanadi!"
        msg_content.caption = None
        msg_content.photo = None
        msg_content.answer = AsyncMock()

        await process_broadcast_content(msg_content, mock_state, db)
        assert msg_content.answer.called
        assert len(PENDING_BROADCASTS) > 0

        # Saqlangan batch_id ni topish
        batch_id = list(PENDING_BROADCASTS.keys())[0]
        assert PENDING_BROADCASTS[batch_id]["audience_type"] == "drivers"
        assert len(PENDING_BROADCASTS[batch_id]["audience"]) == 1

        # Admin yuborishni tasdiqlaydi [🚀 Yuborish]
        cb_send = MagicMock(spec=CallbackQuery)
        cb_send.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        cb_send.data = f"bc_send:{batch_id}"
        cb_send.message = MagicMock()
        cb_send.message.edit_text = AsyncMock()
        cb_send.answer = AsyncMock()
        cb_send.bot = mock_bot

        await cb_broadcast_send(cb_send, db)

        # Haydovchiga xabar yuborilganini tekshirish
        mock_bot.send_message.assert_called_with(
            chat_id=driver_tg_id,
            text="E'tibor bering: ertaga darslar soat 08:30 da boshlanadi!",
            parse_mode=None
        )

        # Broadcasts jadvalida yozuv borligini tekshirish
        conn = await db.get_connection()
        cur_bc = await conn.execute("SELECT * FROM broadcasts WHERE admin_id = ?", (admin_tg_id,))
        bc_row = await cur_bc.fetchone()
        assert bc_row is not None
        bc_dict = dict(bc_row)
        assert bc_dict["audience_type"] == "drivers"
        assert bc_dict["total"] == 1
        assert bc_dict["sent"] == 1
        assert bc_dict["failed"] == 0
        assert bc_dict["text"] == "E'tibor bering: ertaga darslar soat 08:30 da boshlanadi!"
        await conn.close()

    finally:
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass
