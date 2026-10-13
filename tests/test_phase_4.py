import pytest
import os
import uuid
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, Contact, User, CallbackQuery

from database.db import Database
from handlers.parent import process_parent_contact, cb_confirm_child, cb_reject_child
from models.enums import UserRole

@pytest.mark.asyncio
async def test_phase_4_parent_flow():
    test_db = f"test_phase4_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        # 1. Boshlang'ich ma'lumotlar: 2 ta o'quvchi bir xil ota-ona raqami bilan
        v1_id = 1  # Arpaariq
        parent_phone = "+998901234567"
        parent_tg_id = 555666777

        # 1-farzand (Ali)
        child1_id = await db.add_student(
            full_name="Ali Aliyev",
            village_id=v1_id,
            parent_phone_1="90 123 45 67"
        )
        assert child1_id > 0

        # 2-farzand (Vali) - ikkinchi telefon sifatida shu raqam
        child2_id = await db.add_student(
            full_name="Vali Aliyev",
            village_id=v1_id,
            parent_phone_1="90 999 99 99",
            parent_phone_2="998901234567"
        )
        assert child2_id > 0

        # Dars jadvallari qo'shish
        await db.add_schedule(child1_id, 0, "09:00", "11:00")
        await db.add_schedule(child2_id, 1, "14:00", "16:00")

        # -------------------------------------------------------------
        # 2. XAVFSIZLIK TEKSHIRUVI: Begona kontakt yuborilganda rad etilishi
        # (contact.user_id != from_user.id)
        # -------------------------------------------------------------
        fake_contact_msg = MagicMock(spec=Message)
        fake_contact_msg.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        # Begona odamning user_id si: 999999
        fake_contact_msg.contact = Contact(
            phone_number="901234567",
            first_name="Begona",
            user_id=999999
        )
        fake_contact_msg.answer = AsyncMock()

        await process_parent_contact(fake_contact_msg, db)
        
        # Rad etilganligi tekshiruvi:
        fake_contact_msg.answer.assert_called_once()
        answer_text = fake_contact_msg.answer.call_args[0][0]
        assert "Faqat o'zingizning raqamingizni yuborishingiz mumkin" in answer_text
        # Bazada hali ota-ona yaratilmagan bo'lishi kerak
        assert await db.get_parent_by_telegram_id(parent_tg_id) is None

        # -------------------------------------------------------------
        # 3. Tizimda yo'q telefon yuborilganda rad etilishi
        # -------------------------------------------------------------
        unknown_contact_msg = MagicMock(spec=Message)
        unknown_contact_msg.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        unknown_contact_msg.contact = Contact(
            phone_number="+998900000000",  # Bazada yo'q raqam
            first_name="Ota",
            user_id=parent_tg_id
        )
        unknown_contact_msg.answer = AsyncMock()

        await process_parent_contact(unknown_contact_msg, db)
        unknown_contact_msg.answer.assert_called_once()
        unknown_text = unknown_contact_msg.answer.call_args[0][0]
        assert "Raqamingiz tizimdan topilmadi" in unknown_text
        assert await db.get_parent_by_telegram_id(parent_tg_id) is None

        # -------------------------------------------------------------
        # 4. To'g'ri telefon yuborilganda 2 ta bola topilishi va tasdiq so'ralishi
        # -------------------------------------------------------------
        valid_contact_msg = MagicMock(spec=Message)
        valid_contact_msg.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        valid_contact_msg.contact = Contact(
            phone_number="90 123 45 67",
            first_name="Ota",
            user_id=parent_tg_id
        )
        valid_contact_msg.answer = AsyncMock()

        await process_parent_contact(valid_contact_msg, db)

        # Ota-ona bazaga yozilgan bo'lishi kerak
        parent = await db.get_parent_by_telegram_id(parent_tg_id)
        assert parent is not None
        assert parent["phone"] == "+998901234567"
        parent_db_id = parent["id"]

        # 1 ta muvaffaqiyat xabari + 2 ta bola uchun tasdiqlash xabari = jami 3 ta answer
        assert valid_contact_msg.answer.call_count == 3
        msg_calls = [call[0][0] for call in valid_contact_msg.answer.call_args_list]
        assert any("Telefon raqamingiz tasdiqlandi" in m for m in msg_calls)
        assert any("Ali Aliyev" in m for m in msg_calls)
        assert any("Vali Aliyev" in m for m in msg_calls)

        # -------------------------------------------------------------
        # 5. Farzandni tasdiqlash callbacki ([Ha] bosilganda student_parents ga yozilishi)
        # -------------------------------------------------------------
        cb_msg = MagicMock(spec=Message)
        cb_msg.edit_text = AsyncMock()

        cb_query_1 = MagicMock(spec=CallbackQuery)
        cb_query_1.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        cb_query_1.data = f"confirm_ch:{child1_id}:{parent_db_id}"
        cb_query_1.message = cb_msg
        cb_query_1.answer = AsyncMock()

        await cb_confirm_child(cb_query_1, db)
        cb_query_1.answer.assert_called_with("Tasdiqlandi!")
        assert await db.is_student_linked_to_parent(child1_id, parent_db_id) is True

        # Qayta tasdiqlash bosilganda alert berilishi:
        cb_query_1_dup = MagicMock(spec=CallbackQuery)
        cb_query_1_dup.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        cb_query_1_dup.data = f"confirm_ch:{child1_id}:{parent_db_id}"
        cb_query_1_dup.answer = AsyncMock()
        await cb_confirm_child(cb_query_1_dup, db)
        cb_query_1_dup.answer.assert_called_with("Ushbu farzand allaqachon tasdiqlangan!", show_alert=True)

        # 2-farzandni ham tasdiqlash (Bir ota-onaga 2 ta bola bog'lanishi)
        cb_query_2 = MagicMock(spec=CallbackQuery)
        cb_query_2.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        cb_query_2.data = f"confirm_ch:{child2_id}:{parent_db_id}"
        cb_query_2.message = cb_msg
        cb_query_2.answer = AsyncMock()

        await cb_confirm_child(cb_query_2, db)
        assert await db.is_student_linked_to_parent(child2_id, parent_db_id) is True

        # -------------------------------------------------------------
        # 6. Bir ota-onaga 2 ta bola bog'langanligini tekshirish
        # -------------------------------------------------------------
        parent_children = await db.get_parent_students(parent_db_id)
        assert len(parent_children) == 2
        child_names = [c["full_name"] for c in parent_children]
        assert "Ali Aliyev" in child_names
        assert "Vali Aliyev" in child_names

        # -------------------------------------------------------------
        # 7. Audit log (event_log) yozilganligini tekshirish
        # -------------------------------------------------------------
        conn = await db.get_connection()
        cur = await conn.execute("SELECT * FROM event_log WHERE actor_role='parent' ORDER BY id ASC")
        events = [dict(r) for r in await cur.fetchall()]
        await conn.close()

        assert len(events) >= 2
        assert events[0]["action"] == "confirm_child"
        assert events[0]["student_id"] == child1_id
        assert events[1]["action"] == "confirm_child"
        assert events[1]["student_id"] == child2_id

        # -------------------------------------------------------------
        # 8. Rad etish (reject_ch) va uning audit logi
        # -------------------------------------------------------------
        # 3-bola yaratamiz va uni rad etishni sinaymiz
        child3_id = await db.add_student("Begona Bola", v1_id, "901234567")
        cb_query_3 = MagicMock(spec=CallbackQuery)
        cb_query_3.from_user = User(id=parent_tg_id, is_bot=False, first_name="Ota")
        cb_query_3.data = f"reject_ch:{child3_id}:{parent_db_id}"
        cb_query_3.message = cb_msg
        cb_query_3.answer = AsyncMock()

        await cb_reject_child(cb_query_3, db)
        assert await db.is_student_linked_to_parent(child3_id, parent_db_id) is False

        # Rad etilganlik audit logda borligini tekshirish
        conn = await db.get_connection()
        cur = await conn.execute("SELECT * FROM event_log WHERE action='reject_child'")
        reject_events = [dict(r) for r in await cur.fetchall()]
        await conn.close()
        assert len(reject_events) == 1
        assert reject_events[0]["student_id"] == child3_id

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
