import pytest
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, User, Message

from database.db import Database
from services.scheduler_service import SchedulerService
from handlers.admin import cb_admin_approve_trips
from models.enums import TripStatus, TripDirection
from config import settings

@pytest.mark.asyncio
async def test_phase_7_scheduler_and_escalation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", "6273865667")
    test_db = f"test_phase7_{uuid.uuid4().hex[:8]}.db"
    db = Database(test_db)

    try:
        await db.init_db()

        mock_bot = AsyncMock()
        scheduler = SchedulerService(db=db, bot=mock_bot)

        driver_tg_id = 99119911
        admin_tg_id = 6273865667  # settings.admin_ids_list dagi id

        driver_id = await db.add_driver(full_name="Haydovchi Botir", phone="+998901234567", telegram_id=driver_tg_id)
        assert driver_id > 0

        # O'quvchilar va dars jadvallari (Dushanba = 0)
        st1_id = await db.add_student("Temur Aliyev", village_id=1, parent_phone_1="901112233")
        st2_id = await db.add_student("Malika Karimova", village_id=2, parent_phone_1="904445566")
        assert st1_id > 0
        assert st2_id > 0

        await db.add_schedule(st1_id, weekday=0, start_time="09:00", end_time="11:00")
        await db.add_schedule(st2_id, weekday=0, start_time="09:00", end_time="11:00")

        target_date = "2026-10-19"  # 2026-10-19 bu Dushanba (weekday=0)

        # =============================================================
        # 1. 06:30 KUNLIK REJA TUZILISHI VA BAZAGA SAQLANISHI
        # =============================================================
        success, reason, trips = await scheduler.run_daily_plan(target_date=target_date)
        assert success is True
        assert reason == "success"
        assert len(trips) >= 2  # to_center va to_home

        unapproved = await db.get_unapproved_trips(target_date)
        assert len(unapproved) == len(trips)
        for t in unapproved:
            assert t["approved"] == 0
            assert t["status"] == TripStatus.PLANNED.value
            assert t["reminder_count"] == 0

        # =============================================================
        # 2. 07:00 AVTO-TASDIQ ISHLASHI
        # =============================================================
        approved_count = await scheduler.run_auto_approve(target_date=target_date)
        assert approved_count == len(trips)

        unapproved_after = await db.get_unapproved_trips(target_date)
        assert len(unapproved_after) == 0

        all_trips = await db.get_trips_by_date(target_date)
        assert len(all_trips) == len(trips)
        for t in all_trips:
            assert t["approved"] == 1
            assert t["status"] == TripStatus.APPROVED.value

        # Qayta avto-tasdiq chaqirilsa 0 qaytishi
        re_approved = await scheduler.run_auto_approve(target_date=target_date)
        assert re_approved == 0

        # Admin qo'lda tasdiqlash callback tekshiruvi (boshqa sana misolida)
        date_manual = "2026-10-26"
        await scheduler.run_daily_plan(target_date=date_manual)
        unapproved_manual = await db.get_unapproved_trips(date_manual)
        assert len(unapproved_manual) >= 2

        admin_cb = MagicMock(spec=CallbackQuery)
        admin_cb.from_user = User(id=admin_tg_id, is_bot=False, first_name="Admin")
        admin_cb.data = f"admin_approve_trips:{date_manual}"
        admin_cb.message = MagicMock(spec=Message)
        admin_cb.message.edit_text = AsyncMock()
        admin_cb.answer = AsyncMock()

        await cb_admin_approve_trips(admin_cb, db)
        admin_cb.answer.assert_called_with("Reyslar tasdiqlandi!")
        unapproved_manual_after = await db.get_unapproved_trips(date_manual)
        assert len(unapproved_manual_after) == 0

        # =============================================================
        # 3. ESLATMALAR ZANJIRI VA ESKALATSIYA MANTIQI
        # =============================================================
        # Sinov uchun aniq vaqtli bitta approved reys kiritamiz:
        # planned_time = "08:00"
        test_reminder_date = "2026-10-21"
        conn = await db.get_connection()
        cur_rem = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved, reminder_count)
            VALUES (?, 'to_center', ?, '08:00', 'approved', 1, 0)
        """, (test_reminder_date, driver_id))
        test_trip_id = cur_rem.lastrowid
        await conn.execute("""
            INSERT INTO trip_items (trip_id, student_id, village_id, status)
            VALUES (?, ?, 1, 'pending')
        """, (test_trip_id, st1_id))
        await conn.commit()
        await conn.close()

        parent_tg_id = 99118822
        parent_id = await db.add_parent(parent_tg_id, "+998901112233")
        await db.link_student_parent(st1_id, parent_id)
        monkeypatch.setattr(settings, "ADMIN_IDS", str(admin_tg_id))
        mock_bot.send_message.reset_mock()

        def assert_exact_message(chat_id, text):
            assert any(
                call.kwargs.get("chat_id") == chat_id and call.kwargs.get("text") == text
                for call in mock_bot.send_message.await_args_list
            ), f"chat_id={chat_id} uchun kutilgan xabar topilmadi: {text!r}"

        # A) 07:15 (45 daqiqa oldin) -> Hech qanday eslatma ketmasligi kerak
        t_0715 = datetime(2026, 10, 21, 7, 15, 0)
        res_0715 = await scheduler.check_and_send_reminders(now_dt=t_0715)
        assert res_0715["first_reminders"] == 0
        assert res_0715["second_reminders"] == 0
        assert res_0715["escalations"] == 0
        t_check1 = await db.get_trip_details(test_trip_id)
        assert t_check1["reminder_count"] == 0

        # B) 07:30 (Aynan 30 daqiqa oldin) -> 1-ESLATMA ketishi kerak
        t_0730 = datetime(2026, 10, 21, 7, 30, 0)
        res_0730 = await scheduler.check_and_send_reminders(now_dt=t_0730)
        assert res_0730["first_reminders"] == 1
        assert res_0730["second_reminders"] == 0
        assert res_0730["escalations"] == 0
        t_check2 = await db.get_trip_details(test_trip_id)
        assert t_check2["reminder_count"] == 1
        assert t_check2["last_reminder_at"] == "2026-10-21 07:30:00"

        assert_exact_message(
            driver_tg_id,
            f"⏰ <b>1-eslatma: Reys vaqti yaqinlashmoqda!</b>\n\n"
            f"🚐 <b>Reys #{test_trip_id}</b> (Markazga kelish)\n"
            "⏱ Rejalashtirilgan vaqt: <b>08:00</b>\n"
            "👥 O'quvchilar ro'yxati:\n  1. Temur Aliyev — Arpaariq\n\n"
            "Iltimos, harakatni boshlash uchun [🚗 Yo'ldaman] tugmasini bosing:",
        )
        assert_exact_message(
            parent_tg_id,
            "⏰ <b>1-eslatma: Reys vaqti yaqinlashmoqda!</b>\n\n"
            "🚐 Farzandingizning markazga kelish reysi rejalashtirilgan vaqti: <b>08:00</b>.\n"
            "Iltimos, tayyor bo'lib turing.",
        )

        # C) 07:35 -> rejalashtirilgan vaqtga yetmaguncha 2-eslatma ketmaydi.
        t_0735 = datetime(2026, 10, 21, 7, 35, 0)
        res_0735 = await scheduler.check_and_send_reminders(now_dt=t_0735)
        assert res_0735["first_reminders"] == 0
        assert res_0735["second_reminders"] == 0
        assert res_0735["escalations"] == 0
        t_check3 = await db.get_trip_details(test_trip_id)
        assert t_check3["reminder_count"] == 1

        # D) 08:00 -> rejalashtirilgan vaqtda 2-ESLATMA.
        t_0800 = datetime(2026, 10, 21, 8, 0, 0)
        res_0800 = await scheduler.check_and_send_reminders(now_dt=t_0800)
        assert res_0800["first_reminders"] == 0
        assert res_0800["second_reminders"] == 1
        assert res_0800["escalations"] == 0
        t_check4 = await db.get_trip_details(test_trip_id)
        assert t_check4["reminder_count"] == 2
        assert t_check4["last_reminder_at"] == "2026-10-21 08:00:00"
        assert_exact_message(
            driver_tg_id,
            f"⚠️ <b>2-eslatma: Shoshiling! Reys vaqti yetib keldi.</b>\n\n"
            f"🚐 <b>Reys #{test_trip_id}</b> (Markazga kelish)\n"
            "⏱ Rejalashtirilgan vaqt: <b>08:00</b>\n"
            "👥 O'quvchilar ro'yxati:\n  1. Temur Aliyev — Arpaariq\n\n"
            "Iltimos, harakatni boshlash uchun [🚗 Yo'ldaman] tugmasini bosing:",
        )
        assert_exact_message(
            parent_tg_id,
            "⚠️ <b>2-eslatma: Shoshiling! Reys vaqti yetib keldi.</b>\n\n"
            "🚐 Farzandingizning markazga kelish reysi rejalashtirilgan vaqti: <b>08:00</b>.\n"
            "Iltimos, tayyor bo'lib turing.",
        )

        # E) 08:05 -> eskalatsiyaga hali 10 daqiqa bo'lmadi.
        t_0805 = datetime(2026, 10, 21, 8, 5, 0)
        res_0805 = await scheduler.check_and_send_reminders(now_dt=t_0805)
        assert res_0805["first_reminders"] == 0
        assert res_0805["second_reminders"] == 0
        assert res_0805["escalations"] == 0
        t_check5 = await db.get_trip_details(test_trip_id)
        assert t_check5["reminder_count"] == 2

        # F) 08:10 -> admin eskalatsiyasi.
        t_0810 = datetime(2026, 10, 21, 8, 10, 0)
        res_0810 = await scheduler.check_and_send_reminders(now_dt=t_0810)
        assert res_0810["first_reminders"] == 0
        assert res_0810["second_reminders"] == 0
        assert res_0810["escalations"] == 1
        t_check6 = await db.get_trip_details(test_trip_id)
        assert t_check6["reminder_count"] == 3
        assert t_check6["last_reminder_at"] == "2026-10-21 08:10:00"
        assert_exact_message(
            admin_tg_id,
            "⚠️ <b>DIQQAT! Reysga javob berilmadi (Eskalatsiya)!</b>\n\n"
            "Haydovchi: <b>Haydovchi Botir</b> (Tel: +998901234567)\n"
            f"Reys: <b>#{test_trip_id}</b> (Markazga kelish)\n"
            "Vaqti: <b>08:00</b>\n\n"
            "Haydovchi 2 ta eslatmaga ham javob bermadi va [🚗 Yo'ldaman] tugmasini bosmadi!",
        )

        # G) Eskalatsiyadan so'ng reminder_count=3 bo'lgani uchun takroriy xabar ketmaydi.
        t_0815 = datetime(2026, 10, 21, 8, 15, 0)
        res_0815 = await scheduler.check_and_send_reminders(now_dt=t_0815)
        assert res_0815["first_reminders"] == 0
        assert res_0815["second_reminders"] == 0
        assert res_0815["escalations"] == 0
        t_check7 = await db.get_trip_details(test_trip_id)
        assert t_check7["reminder_count"] == 3

        delayed, _, _ = await db.apply_trip_delay(test_trip_id, 15, admin_tg_id)
        assert delayed is True
        t_after_delay = await db.get_trip_details(test_trip_id)
        assert t_after_delay["reminder_count"] == 0
        assert t_after_delay["last_reminder_at"] is None

        # =============================================================
        # 4. "YO'LDAMAN" BOSILGANDA ESLATMALAR TO'XTASHI
        # =============================================================
        # Yangi reys ochamiz, unga status 'on_way' beramiz
        test_on_way_date = "2026-10-22"
        conn = await db.get_connection()
        cur_ow = await conn.execute("""
            INSERT INTO trips (date, direction, driver_id, planned_time, status, approved, reminder_count)
            VALUES (?, 'to_center', ?, '08:00', 'on_way', 1, 0)
        """, (test_on_way_date, driver_id))
        ow_trip_id = cur_ow.lastrowid
        await conn.commit()
        await conn.close()

        # 07:35 da (vaqt o'tsa ham) status 'on_way' bo'lgani uchun eslatma bormaydi
        t_ow = datetime(2026, 10, 22, 7, 35, 0)
        res_ow = await scheduler.check_and_send_reminders(now_dt=t_ow)
        assert res_ow["first_reminders"] == 0
        assert res_ow["second_reminders"] == 0
        assert res_ow["escalations"] == 0
        ow_check = await db.get_trip_details(ow_trip_id)
        assert ow_check["reminder_count"] == 0

        # =============================================================
        # 5. QAYTA HISOBLASHDAN HIMOYALANISH (ON_WAY YOKI DONE REYS BOR BO'LSA)
        # =============================================================
        # test_on_way_date sanasida allaqachon 'on_way' reys bor
        re_plan_ok, re_plan_reason, re_plan_trips = await scheduler.run_daily_plan(target_date=test_on_way_date)
        assert re_plan_ok is False
        assert re_plan_reason == "active_trips_exist"
        assert len(re_plan_trips) == 0

        # Baza zaxiralash (backup) testi
        backup_path = await scheduler.run_backup_db(backup_dir=str(tmp_path / "backups"))
        assert os.path.exists(backup_path)

    finally:
        if os.path.exists(test_db):
            os.remove(test_db)


@pytest.mark.asyncio
async def test_cancelled_trip_is_not_auto_approved(tmp_path):
    db = Database(str(tmp_path / "cancelled_trip_auto_approve.db"))
    await db.init_db()

    driver_id = await db.add_driver(
        full_name="Sinov haydovchisi",
        phone="+998901234567",
        telegram_id=99112233,
    )
    student_id = await db.add_student(
        "Sinov o'quvchisi",
        village_id=1,
        parent_phone_1="+998901112233",
    )
    await db.add_schedule(student_id, weekday=0, start_time="09:00", end_time="11:00")

    scheduler = SchedulerService(db=db)
    target_date = "2026-10-26"
    success, reason, _ = await scheduler.run_daily_plan(target_date=target_date)
    assert success is True
    assert reason == "success"

    trips = await db.get_trips_by_date(target_date)
    assert trips
    cancelled_trip_id = trips[0]["id"]
    assert await db.cancel_trip(cancelled_trip_id, admin_id=6273865667) is True

    await scheduler.run_auto_approve(target_date=target_date)

    cancelled_trip = await db.get_trip_details(cancelled_trip_id)
    assert cancelled_trip["status"] == "cancelled"
    assert cancelled_trip["approved"] == 0
    unapproved_trips = await db.get_unapproved_trips(target_date)
    assert cancelled_trip_id not in {trip["id"] for trip in unapproved_trips}
