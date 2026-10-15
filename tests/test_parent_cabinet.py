import pytest
import os
import uuid
from html import unescape
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, User, Message
from aiogram.fsm.context import FSMContext

from database.db import Database
from config import settings
from handlers.parent import (
    get_parent_menu_kb,
    parent_track_children,
    parent_center_contact,
    parent_report_absence_start,
    cb_absent_select,
    process_absence_reason,
    ParentStates
)
from models.enums import TripItemStatus

@pytest.mark.asyncio
async def test_parent_cabinet_and_fsm_absence():
    test_db = f"test_parent_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        # 1. PRAGMA orqali 'reason' ustuni mavjudligini tekshirish
        conn = await db.get_connection()
        cur_col = await conn.execute("PRAGMA table_info(absences)")
        columns = [row[1] for row in await cur_col.fetchall()]
        assert "reason" in columns, "absences jadvalida 'reason' ustuni mavjud emas!"
        await conn.close()

        # 2. Reply menu tugmalari to'g'riligini tekshirish
        menu_kb = get_parent_menu_kb()
        all_buttons = [btn.text for row in menu_kb.keyboard for btn in row]
        assert "📍 Farzandim qayerda?" in all_buttons
        assert "🚫 Bugun kelmaydi" in all_buttons
        assert "📅 Dars jadvali" in all_buttons
        assert "📞 Markaz bilan aloqa" in all_buttons

        mock_bot = AsyncMock()
        admin_tg_id = settings.admin_ids_list[0] if settings.admin_ids_list else 6273865667
        parent_tg_id = 99887766
        driver_tg_id = 88776655

        # 3. Foydalanuvchilarni yaratish
        driver_id = await db.add_driver(driver_tg_id, "Haydovchi Botir", "+998901234567")
        parent_id = await db.add_parent(parent_tg_id, "+998909876543")

        st1_id = await db.add_student("Ali Aliyev", village_id=1, parent_phone_1="909876543")
        st2_id = await db.add_student("Vali Aliyev", village_id=1, parent_phone_1="909876543")

        await db.link_student_parent(st1_id, parent_id)
        await db.link_student_parent(st2_id, parent_id)

        today_str = datetime.now().strftime("%Y-%m-%d")

        # 4. Reys yaratish: st1 (pending), st2 (picked_up)
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
        # 5. "📍 Farzandim qayerda?" TEKSHIRUVI
        # =============================================================
        msg_track = MagicMock(spec=Message)
        msg_track.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        msg_track.answer = AsyncMock()

        await parent_track_children(msg_track, db)
        assert msg_track.answer.called
        track_text = msg_track.answer.call_args[0][0]
        assert "Ali Aliyev" in track_text
        assert "Kutmoqda" in track_text
        assert "Vali Aliyev" in track_text
        assert "Mashinada" in track_text

        # =============================================================
        # 6. "📞 Markaz bilan aloqa" TEKSHIRUVI
        # =============================================================
        msg_contact = MagicMock(spec=Message)
        msg_contact.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        msg_contact.answer = AsyncMock()

        await parent_center_contact(msg_contact)
        assert "+998 77 260 20 20" in msg_contact.answer.call_args[0][0]

        # =============================================================
        # 7. "🚫 Bugun kelmaydi" FSM VA SABAB BILAN BELGILASH
        # =============================================================
        # 7.1 Ikki farzand bo'lganda tanlov chiqarish
        mock_state = AsyncMock(spec=FSMContext)
        mock_state_data = {}

        async def fake_update_data(**kwargs):
            mock_state_data.update(kwargs)

        async def fake_get_data():
            return mock_state_data

        mock_state.update_data = AsyncMock(side_effect=fake_update_data)
        mock_state.get_data = AsyncMock(side_effect=fake_get_data)
        mock_state.set_state = AsyncMock()
        mock_state.clear = AsyncMock(side_effect=lambda: mock_state_data.clear())

        msg_absent = MagicMock(spec=Message)
        msg_absent.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        msg_absent.answer = AsyncMock()

        await parent_report_absence_start(msg_absent, mock_state, db)
        assert "Qaysi farzandingiz" in msg_absent.answer.call_args[0][0]

        # 7.2 Ota st1 (Ali) ni tanlaydi
        cb_select = MagicMock(spec=CallbackQuery)
        cb_select.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        cb_select.data = f"absent_select:{st1_id}"
        cb_select.message = MagicMock()
        cb_select.message.edit_text = AsyncMock()
        cb_select.answer = AsyncMock()

        await cb_absent_select(cb_select, mock_state, db)
        assert mock_state_data["absent_student_id"] == st1_id
        mock_state.set_state.assert_called_with(ParentStates.waiting_for_absence_reason)

        # 7.3 Ota izoh yozadi: HTML belgilar literal matn bo'lib ko'rinishi kerak
        reason_text = "<b>Shamollab</b> & shifokorga olib boramiz"
        msg_reason = MagicMock(spec=Message)
        msg_reason.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        msg_reason.text = reason_text
        msg_reason.bot = mock_bot
        msg_reason.answer = AsyncMock()

        await process_absence_reason(msg_reason, mock_state, db)

        # Ota-onaga tasdiq xabari borganini tekshirish
        assert "bugun darsga kelmaydi deb belgilandi" in msg_reason.answer.call_args[0][0]

        # Bazada absences jadvalida reason saqlanganini tekshirish
        conn = await db.get_connection()
        cur_a = await conn.execute("SELECT * FROM absences WHERE student_id = ? AND date = ?", (st1_id, today_str))
        a_row = await cur_a.fetchone()
        assert a_row is not None
        assert a_row["reason"] == reason_text

        # trip_items holati absent bo'lganini tekshirish
        cur_ti = await conn.execute("SELECT status FROM trip_items WHERE id = ?", (item1_id,))
        assert (await cur_ti.fetchone())[0] == "absent"
        await conn.close()

        # Haydovchiga xabar borganini tekshirish (izoh bilan)
        mock_bot.send_message.assert_any_call(
            chat_id=driver_tg_id,
            text=f"ℹ️ <b>Ali Aliyev</b> (Arpaariq) bugun kelmaydi deb belgilandi.\n💬 <b>Sababi:</b> &lt;b&gt;Shamollab&lt;/b&gt; &amp; shifokorga olib boramiz",
            parse_mode="HTML"
        )

        # Adminga xabar borganini tekshirish
        admin_call_found = False
        for call in mock_bot.send_message.call_args_list:
            if call[1].get("chat_id") == admin_tg_id and "Kelmaslik xabarnomasi" in call[1].get("text", ""):
                admin_call_found = True
                assert "Ali Aliyev" in call[1]["text"]
                encoded_reason = "&lt;b&gt;Shamollab&lt;/b&gt; &amp; shifokorga olib boramiz"
                assert encoded_reason in call[1]["text"]
                assert unescape(encoded_reason) == reason_text
                assert call[1]["parse_mode"] == "HTML"
        assert admin_call_found, "Adminga kelmaslik xabarnomasi bormadi!"

        # =============================================================
        # 8. MASHINADA BO'LGAN (st2, picked_up) BOLA RAD ETILISHI
        # =============================================================
        mock_state_data["absent_student_id"] = st2_id
        msg_reason_st2 = MagicMock(spec=Message)
        msg_reason_st2.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        msg_reason_st2.text = "Kechikib qoldik"
        msg_reason_st2.bot = mock_bot
        msg_reason_st2.answer = AsyncMock()

        await process_absence_reason(msg_reason_st2, mock_state, db)
        assert "Farzandingiz allaqachon mashinaga chiqqan, uni bekor qilib bo'lmaydi." in msg_reason_st2.answer.call_args[0][0]

    finally:
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_parent_status_prefers_active_to_center_trip_over_pending_home_trip():
    test_db = f"test_parent_status_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        await db.init_db()
        conn = await db.get_connection()
        cur = await conn.execute("INSERT INTO zones (name) VALUES ('Test zona')")
        zone_id = cur.lastrowid
        cur = await conn.execute(
            "INSERT INTO villages (name, zone_id, travel_minutes, route_order) VALUES ('Test mahalla', ?, 10, 1)",
            (zone_id,),
        )
        village_id = cur.lastrowid
        await conn.commit()
        await conn.close()

        student_id = await db.add_student("Test o'quvchi", village_id, "901234567")
        conn = await db.get_connection()
        cur = await conn.execute(
            "INSERT INTO trips (date, direction, planned_time, status) VALUES (?, 'to_center', '08:00', 'on_way')",
            (today,),
        )
        center_trip_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO trip_items (trip_id, student_id, village_id, status) VALUES (?, ?, ?, 'picked_up')",
            (center_trip_id, student_id, village_id),
        )
        cur = await conn.execute(
            "INSERT INTO trips (date, direction, planned_time, status) VALUES (?, 'to_home', '17:00', 'planned')",
            (today,),
        )
        home_trip_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO trip_items (trip_id, student_id, village_id, status) VALUES (?, ?, ?, 'pending')",
            (home_trip_id, student_id, village_id),
        )
        await conn.commit()
        await conn.close()

        current = await db.get_student_current_status(student_id, today)

        assert current["trip"]["trip_id"] == center_trip_id
        assert current["status_code"] == "picked_up"
        assert current["status_text"] == "Mashinada"

        conn = await db.get_connection()
        await conn.execute("UPDATE trips SET status = 'at_center' WHERE id = ?", (center_trip_id,))
        await conn.commit()
        await conn.close()
        arrived = await db.get_student_current_status(student_id, today)
        assert arrived["status_text"] == "Markazga yetib kelgan"
    finally:
        for suffix in ("", "-wal", "-shm"):
            path = test_db + suffix
            if os.path.exists(path):
                os.remove(path)
